# Requirements Document

## Introduction

本功能为「多媒体展示系统-网络游鱼」(CyberFish) 增加「自动屏幕相邻关系探测」能力。项目当前已经实现 UDP 广播自动发现局域网内其他主机、鱼跨屏移交（含 ack/重试/超时回退/去重）、控制台 UI 与参数控制，但屏幕之间的相邻关系（topology：left/right/up/down 四个方向各自对应一个邻居 node_id）仍然完全依赖用户在控制台手动点选主机并指定方向。

本功能引入「拓扑协调器」(Topology_Coordinator)，使各主机能够在去中心、对等的前提下，通过 UDP 广播自动协商屏幕之间的相邻方向，自动维护方向互逆一致性与方向唯一性，并在主机动态加入/退出时自动更新拓扑。自动探测结果持久化到 config.json 的 topology 字段，与现有跨屏移交逻辑保持兼容。系统保留手动校准与自动模式开关，并在控制台反映当前模式与拓扑状态。

> 说明：部分策略（如自动模式默认开关、手动覆盖的锁定语义、相邻方向的初始判定依据）在本文档中给出了默认取值，可在评审或设计阶段进一步确认调整。

## Glossary

- **CyberFish_Node**：运行 CyberFish 程序的单台主机实例，拥有唯一的 node_id 与一块展示屏幕。
- **Topology_Coordinator**：本功能新增的拓扑协调器组件，负责自动协商、维护与持久化本机的相邻方向关系。
- **NetworkManager**：现有网络管理器，通过 UDP 广播发现 Peer、收发 hello/fish_transfer/transfer_ack/fish_state 消息，并维护带 TTL 的 Peer 表。
- **Console**：现有控制台 UI 组件，展示运行状态并提供交互按钮。
- **Config_Store**：负责读写 config.json 的配置存储模块。
- **Transfer_Engine**：现有跨屏移交逻辑，依据 topology 判定鱼可从哪些边游出并将鱼移交给对应方向邻居。
- **Peer**：被 NetworkManager 发现且未过期的其他在线 CyberFish_Node，包含 node_id、hostname、address、port、screen_size、last_seen。
- **Direction（方向）**：相邻关系的四个取值之一：left、right、up、down。
- **Inverse_Direction（互逆方向）**：方向的相反方向，left 与 right 互逆、up 与 down 互逆。
- **Neighbor（邻居）**：在某一 Direction 上与本机相邻的 Peer。
- **Topology（拓扑）**：本机维护的方向到邻居 node_id 的映射，结构为 {left, right, up, down}，每个方向的值为邻居 node_id 或 null。
- **Auto_Mode（自动模式）**：拓扑协调器自动维护 Topology 的工作模式，由 config.json 的 auto_topology 布尔字段控制。
- **Manual_Override（手动覆盖）**：用户在控制台为某一 Direction 手动指定的邻居关系。
- **Negotiation_Message（协商消息）**：拓扑协调器之间通过 UDP 广播交换的、用于协商相邻方向的消息。
- **Convergence（收敛）**：一次拓扑变更后，相关各 CyberFish_Node 的 Topology 达到方向互逆一致且方向唯一的稳定状态。

## Requirements

### Requirement 1: 自动模式开关

**User Story:** 作为用户，我希望能够开启或关闭自动拓扑探测，以便在自动协商与手动校准之间切换。

#### Acceptance Criteria

1. THE Config_Store SHALL 在 config.json 中持久化一个名为 auto_topology 的布尔字段，用于表示 Auto_Mode 的开启状态。
2. IF Config_Store 读取 config.json 时 auto_topology 字段缺失或其值不是合法布尔值（true 或 false），THEN THE Config_Store SHALL 将 auto_topology 初始化为 true 并写回 config.json。
3. WHEN 用户在 Console 切换 Auto_Mode，THE Topology_Coordinator SHALL 在 1 秒内更新内存中的 Auto_Mode 状态并通过 Config_Store 持久化到 config.json。
4. WHILE Auto_Mode 处于开启状态，THE Topology_Coordinator SHALL 至少每 5 秒自动协商一次屏幕相邻关系。
5. WHILE Auto_Mode 处于开启状态，WHEN 检测到屏幕相邻关系发生变化，THE Topology_Coordinator SHALL 在 1 秒内将 Topology 更新为最新协商结果。
6. WHILE Auto_Mode 处于关闭状态，THE Topology_Coordinator SHALL 停止自动协商并保持 Topology 的当前取值不变。
7. IF Topology_Coordinator 通过 Config_Store 持久化 auto_topology 到 config.json 失败，THEN THE Topology_Coordinator SHALL 保留当前内存中的 Auto_Mode 状态，并在 Console 显示指示持久化失败的错误提示。

### Requirement 2: 自动方向分配

**User Story:** 作为用户，我希望系统自动把发现的主机分配到合适的相邻方向，以便无需手动点选即可建立拓扑。

#### Acceptance Criteria

1. WHILE Auto_Mode 处于开启状态且 Topology 中至少存在一个取值为 null 的 Direction，WHEN NetworkManager 发现一个尚未出现在 Topology 中的 Peer，THE Topology_Coordinator SHALL 通过 Negotiation_Message 与该 Peer 协商，并按 left、right、up、down 的固定顺序选取第一个 null Direction 为其分配。
2. WHILE Auto_Mode 处于开启状态，WHEN NetworkManager 发现一个新 Peer 且四个 Direction 均已被非 null 的邻居占用，THE Topology_Coordinator SHALL 保持 Topology 不变，不为该新发现的 Peer 发起方向分配。
3. WHEN 一个 Peer 被分配到某个 Direction，THE Topology_Coordinator SHALL 在该 Peer 与本机之间完成 Convergence 后，将本机 Topology 中该 Direction 的值设为该 Peer 的 node_id。
4. IF 一个 Peer 的方向分配协商在 10 秒内未达成 Convergence，THEN THE Topology_Coordinator SHALL 保持对应 Direction 的值为 null，不将该 Peer 的 node_id 写入 Topology。
5. THE Topology_Coordinator SHALL 保证同一个 Peer 的 node_id 在 Topology 中最多出现在一个 Direction 上。

### Requirement 3: 方向互逆一致性

**User Story:** 作为用户，我希望相邻关系在两台主机上互逆一致，以便鱼能够在两块屏幕之间双向游动。

#### Acceptance Criteria

1. WHEN 本机将 Peer P 分配到 Direction D，THE Topology_Coordinator SHALL 在 1 秒内通过 Negotiation_Message 通知 P 将本机 node_id 设置到 D 的 Inverse_Direction 上。
2. WHEN 本机与所有相邻 Peer 在连续 5 秒内没有发生任何相邻关系变更且全部相邻 Peer 均已确认（即达成 Convergence），THE Topology_Coordinator SHALL 满足如下不变式：若本机在 Direction D 上的邻居为 Peer P，则 P 在 D 的 Inverse_Direction 上的邻居为本机 node_id。
3. IF 一个 Peer 在 Negotiation_Message 中声明的相邻方向与本机记录的方向不互逆，THEN THE Topology_Coordinator SHALL 采用 node_id 字典序较小一方所声明的取值更新本机 Topology，使该相邻关系恢复互逆一致。
4. IF 本机发出的相邻关系 Negotiation_Message 在 2 秒内未收到目标 Peer 的确认，THEN THE Topology_Coordinator SHALL 最多重发该消息 3 次，并在仍未确认时将该相邻关系标记为未确认且不计入 Convergence 判定。
5. WHEN Convergence 完成，THE Topology_Coordinator SHALL 满足唯一性不变式：每个 Direction 至多对应一个 Peer，且同一个 Peer 不同时出现在多个 Direction 上。

### Requirement 4: 方向唯一性与冲突解决

**User Story:** 作为用户，我希望每个方向最多只对应一台邻居主机，并且在多台主机争抢同一方向时有确定的结果，以便拓扑不出现歧义。

#### Acceptance Criteria

1. THE Topology_Coordinator SHALL 保证 Topology 中每个 Direction 在任一时刻最多对应一个非 null 的邻居 node_id。
2. IF 在某 Direction 完成 Convergence 之前存在两个或更多 Peer 请求占用同一个 Direction，THEN THE Topology_Coordinator SHALL 选取这些 Peer 中按字符串字典序（按 Unicode 码点逐字符比较）最小的 node_id 作为唯一胜出 Peer，并将该 Direction 的邻居 node_id 设为该胜出 Peer 的 node_id。
3. WHEN Topology_Coordinator 为某 Peer 尝试方向分配，THE Topology_Coordinator SHALL 按 left、right、up、down 的固定顺序选取第一个当前取值为 null 的 Direction 分配给该 Peer。
4. WHERE 多个 CyberFish_Node 接收到相同的 Negotiation_Message 集合，THE Topology_Coordinator SHALL 在各 CyberFish_Node 上产生完全相同的冲突解决结果，即相同的胜出 node_id 与相同的方向占用结果。
5. WHEN 某 Peer 在某 Direction 的冲突解决中落败，THE Topology_Coordinator SHALL 无条件为该落败 Peer 触发回退分配，并按 left、right、up、down 的固定顺序为其选取另一个当前取值为 null 的 Direction 进行分配。
6. IF 某落败 Peer 触发回退分配时四个 Direction 均已被非 null 邻居占用，THEN THE Topology_Coordinator SHALL 保持 Topology 不变，不为该落败 Peer 分配方向。

### Requirement 5: 主机动态加入

**User Story:** 作为用户，我希望新主机上线后自动被纳入拓扑，以便扩展展示墙时无需重新配置。

#### Acceptance Criteria

1. WHILE Auto_Mode 处于开启状态且 Topology 中至少存在一个取值为 null 的 Direction，WHEN 一个新的 CyberFish_Node 上线并被 NetworkManager 收到其首个 hello 广播而发现，THE Topology_Coordinator SHALL 在该发现时刻起 10 秒内尝试为其分配 Direction 并完成 Convergence。
2. WHILE Auto_Mode 处于开启状态，WHEN 一个先前被分配方向的 Peer 连续 DISCOVERY_TTL_SECONDS（8 秒）未收到广播而被判定离线后再次被发现，THE Topology_Coordinator SHALL 自该再次发现时刻起 10 秒内重新为其分配一个 null Direction 并达成 Convergence。
3. IF 一个新发现的 CyberFish_Node 上线时四个 Direction 均已被非 null 邻居占用，THEN THE Topology_Coordinator SHALL 保持 Topology 不变，不为其分配方向。

### Requirement 6: 主机动态退出与方向释放

**User Story:** 作为用户，我希望某台主机下线后其占用的方向被自动释放，以便其他主机能够重新使用该方向。

#### Acceptance Criteria

1. IF Topology 中某 Direction 的邻居 node_id 对应的 Peer 已被 NetworkManager 判定为过期（连续 DISCOVERY_TTL_SECONDS（8 秒）未收到该 Peer 的广播），THEN THE Topology_Coordinator SHALL 在该过期判定生效后 1 秒内将该 Direction 的值设为 null。
2. WHEN 某 Direction 因邻居离线被设为 null，THE Topology_Coordinator SHALL 将该 Direction 标记为可用，使其在设为 null 后 10 秒内可被分配给其他在线 Peer。
3. WHEN 某 Direction 因邻居离线被设为 null，THE Topology_Coordinator SHALL 在 1 秒内通过 Config_Store 将更新后的 Topology 持久化到 config.json。
4. IF Config_Store 将更新后的 Topology 写入 config.json 失败，THEN THE Topology_Coordinator SHALL 保留内存中已更新的 Topology（该 Direction 保持为 null），并输出指示持久化失败的错误信息。

### Requirement 7: 拓扑持久化与配置兼容

**User Story:** 作为用户，我希望自动探测得到的拓扑被保存下来，并且配置文件保持原有结构，以便重启后沿用结果且与现有逻辑兼容。

#### Acceptance Criteria

1. WHEN Topology 中任一 Direction 的取值与 config.json 中已持久化的对应取值不同，THE Topology_Coordinator SHALL 通过 Config_Store 将更新后的 Topology 写入 config.json 的 topology 字段。
2. THE Config_Store SHALL 保持 topology 字段恰好包含 left、right、up、down 四个 Direction 键，且每个 Direction 的取值为非空 node_id 字符串或 null。
3. WHEN Config_Store 读取 config.json 后再次写回，THE Config_Store SHALL 使写回后每个 Direction 的取值与读取时逐方向相等（字符串完全相等或同为 null），且该等价判定不依赖键顺序或文本格式。
4. WHEN Config_Store 写入 topology 字段时，THE Config_Store SHALL 保持 config.json 中其它字段的取值不变。
5. WHILE Auto_Mode 处于开启状态，WHEN 程序启动，THE Topology_Coordinator SHALL 将 config.json 中已持久化的 topology 作为协商起点加载。
6. IF 启动时 config.json 的 topology 字段缺失或结构非法，THEN THE Config_Store SHALL 回退为四个 Direction 均为 null 的起点并继续启动。
7. IF Config_Store 写入 config.json 失败，THEN THE Config_Store SHALL 保护原 config.json 文件不被破坏，并返回指示写入失败的结果。

### Requirement 8: 与跨屏移交兼容

**User Story:** 作为用户，我希望自动探测得到的拓扑能直接驱动鱼的跨屏移交，以便鱼按照自动建立的相邻关系游动。

#### Acceptance Criteria

1. THE Topology_Coordinator SHALL 以现有 {left, right, up, down} 到 node_id 的映射形式向 Transfer_Engine 提供最新的 Topology，使任一 Topology 变更在下一次移交判定中即被反映。
2. WHILE 某 Direction 的邻居 node_id 对应一个未被 NetworkManager 判定为过期（DISCOVERY_TTL_SECONDS 内仍有广播）的 Peer，THE Transfer_Engine SHALL 将该 Direction 视为可移交边。
3. WHEN 一条鱼到达本机屏幕在某可移交边方向上的边界并越过该边界，THE Transfer_Engine SHALL 将该鱼移交给该 Direction 上的邻居 Peer。
4. WHILE 某 Direction 的值为 null，或该 Direction 的邻居 node_id 对应的 Peer 已被 NetworkManager 判定为过期，THE Transfer_Engine SHALL 不将该 Direction 视为可移交边。
5. IF 一条鱼到达本机屏幕在某非可移交边方向上的边界，THEN THE Transfer_Engine SHALL 不移交该鱼，并将该鱼保留在本机屏幕内继续游动。

### Requirement 9: 手动覆盖与自动模式共存

**User Story:** 作为用户，我希望在自动模式下仍能手动指定某个方向的邻居，以便在自动结果不符合实际摆放时进行校正。

#### Acceptance Criteria

1. WHERE Auto_Mode 处于开启状态，WHEN 用户在 Console 为某 Direction 手动指定一个合法的邻居 Peer（其 node_id 不等于本机 node_id，且为当前已知的在线或曾在线节点），THE Topology_Coordinator SHALL 接受该 Manual_Override，并在 1 秒（1000 毫秒）内将 Topology 中该 Direction 的取值更新为该 Peer，覆盖该 Direction 当前的任意取值（无论来自自动协商或既有 Manual_Override）。
2. WHILE 某 Direction 存在生效的 Manual_Override，THE Topology_Coordinator SHALL 在自动协商中保持该 Direction 的取值不被自动改写。
3. WHEN 用户手动将某 Direction 的邻居指定为某 Peer，THE Topology_Coordinator SHALL 通过 Negotiation_Message 通知该 Peer 在 Inverse_Direction 上将邻居设置为本机 node_id。
4. WHEN 一个 Manual_Override 对应的邻居 Peer 连续未响应（未收到其心跳或协商响应）超过 DISCOVERY_TTL_SECONDS（8 秒），THE Topology_Coordinator SHALL 将该 Direction 的取值设为 null 并解除该 Manual_Override 的锁定。
5. IF 用户在 Console 为某 Direction 手动指定的 Peer 的 node_id 等于本机 node_id，或为未知节点，THEN THE Topology_Coordinator SHALL 拒绝该 Manual_Override，保持该 Direction 的原有取值不变，并向 Console 返回指示拒绝原因的错误提示。
6. IF 手动指定后在 DISCOVERY_TTL_SECONDS（8 秒）内未能成功通过 Negotiation_Message 通知该 Peer（例如该 Peer 处于离线状态），THEN THE Topology_Coordinator SHALL 在本机保留该 Manual_Override 并在该 Direction 上标记为待确认状态，且在该 Peer 重新上线后的 DISCOVERY_TTL_SECONDS（8 秒）内重发 Negotiation_Message。

### Requirement 10: 控制台状态展示

**User Story:** 作为用户，我希望在控制台看到当前是自动还是手动模式以及拓扑状态，以便确认系统的工作情况。

#### Acceptance Criteria

1. THE Console SHALL 以两种可区分的视觉状态分别显示 Auto_Mode 处于开启或关闭。
2. THE Console SHALL 为 left、right、up、down 四个 Direction 各自显示其邻居 node_id 以及该邻居是否在线，其中"是否在线"以 NetworkManager 当前未过期（DISCOVERY_TTL_SECONDS 内）的 Peer 集合为判定依据。
3. WHERE 某 Direction 的取值为 null，THE Console SHALL 为该 Direction 显示"无邻居"占位而非 node_id。
4. WHEN Topology 因自动协商发生变更，THE Console SHALL 在该变更后 1 秒内的下一帧渲染中反映更新后的 Topology。
5. THE Console SHALL 提供一个用于切换 Auto_Mode 的交互控件，且该控件的视觉状态与当前 Auto_Mode 一致。
6. WHEN 用户点击切换 Auto_Mode 的交互控件，THE Console SHALL 在 1 秒内更新该控件的视觉状态以反映切换后的 Auto_Mode。

### Requirement 11: 去中心通信与稳定性约束

**User Story:** 作为用户，我希望自动拓扑探测以去中心、低延迟的方式运行且足够稳定，以便在普通 PC 组成的局域网中可靠工作。

#### Acceptance Criteria

1. THE Topology_Coordinator SHALL 仅通过 NetworkManager 的 UDP 广播交换 Negotiation_Message，不依赖任何中心服务器节点。
2. WHEN 被 NetworkManager 发现的 Peer 集合发生一次变更（新增或移除一个 Peer）且其后不再发生新的集合变更，THE Topology_Coordinator SHALL 在该次变更后的 10 秒内使相关 CyberFish_Node 的 Topology 达到 Convergence。
3. IF 收到的 Negotiation_Message 无法按 UTF-8 JSON 解析，或缺少消息类型字段、发送方 node_id 字段、协商所需的方向字段中的任意一项，THEN THE Topology_Coordinator SHALL 丢弃该消息、保持当前 Topology 不变并继续运行。
4. IF 收到的 Negotiation_Message 的发送方 node_id 等于本机 node_id，THEN THE Topology_Coordinator SHALL 忽略该消息并保持当前 Topology 不变。
5. THE Topology_Coordinator SHALL 将 Negotiation_Message 的广播频率限制为每秒不超过 1 轮（与 hello 广播同周期），以不改变现有 hello/fish_transfer/fish_state 的发送时序。
6. THE Topology_Coordinator SHALL 保证单条 Negotiation_Message 序列化后的字节数不超过 MAX_DATAGRAM_BYTES（65507 字节）。
