# 深圳出租车行程距离计算器

基于本地部署的 OSRM 路由引擎，从出租车 GPS 轨迹数据中提取完整行程，并计算每段行程的实际驾车距离与耗时。

## 项目原理

### 核心思路

本项目利用 **OSRM（Open Source Routing Machine，开源路由引擎）** 在本地搭建一个路由服务，将出租车的上下客坐标输入引擎，获取两点间的真实道路驾车距离。

### 数据流程

```
出租车GPS数据 (CSV)
    │  VehicleNum, Stime, Lng, Lat, OpenStatus, Speed
    │
    ▼
[行程提取] trip_pair_filter.py  栈算法严格配对
    │  OpenStatus 0→1: 入栈（上车点）
    │  OpenStatus 1→0: 出栈配对（下车点）
    │  若首条记录为 1：初始载客段忽略（上车点不可知）
    │  输出：taxi_trip_clean_paired_trips.csv
    │
    ▼
[距离计算] trip_osrm_distance.py  调用 OSRM HTTP API
    │  GET /route/v1/driving/{lng},{lat};{lng},{lat}
    │  多线程并发请求
    │  输出：taxi_trip_clean_paired_trips_trip_distances.csv
    │
    ▼
[结果验证] validate_trip_pairing.py  校验配对正确性
    │  用栈算法重建预期配对，与距离文件逐条比对
    │  报告: 缺失、冗余、偏移等配对异常
```

### 行程提取算法：栈配对

不同于简单的「按索引一一对应」，本工具使用**栈（Stack）**算法确保上下车事件的严格配对：

```
遍历每辆车的 GPS 记录（按时间排序）：
  - OpenStatus 0→1 转换 → 上车点，入栈
  - OpenStatus 1→0 转换 → 下车点，从栈中弹出最近的上车点配对
  - 车辆首条记录若为 1（载客中）→ 不处理，初始上车点不可知
```

这样能正确处理以下边界情况：
- 车辆数据窗口起始时已在载客状态（首条 OpenStatus=1）
- 连续多条记录保持同一状态（取转换边界，不重复配对）
- 车辆最后一段行程未下车（栈中剩余，单独报告）

### 技术栈

| 组件 | 说明 |
|------|------|
| **OSRM** | 开源路由引擎，基于 OpenStreetMap 路网数据，使用 MLD（Multi-Level Dijkstra）算法实现毫秒级路径规划 |
| **地图数据** | 广东省 OpenStreetMap 路网数据（`.osm.pbf` 格式，约 160MB），来源 [Geofabrik](https://download.geofabrik.de/asia/china.html) |
| **路由配置** | 使用 OSRM 内置的 `car.lua` 驾车配置，支持车行道路、限速、转向限制等 |
| **计算脚本** | Python 3 + pandas（数据处理）+ requests（HTTP 调用），`ThreadPoolExecutor` 多线程并发 |
| **容器化** | Docker 部署 `osrm/osrm-backend` 镜像，端口 5000 提供路由 API 服务 |

### OSRM 地图预处理流程

```
.osm.pbf 原始数据
    │  osrm-extract   → 提取路网图结构（.osrm 文件）
    │  osrm-partition → 对图进行分区（MLD 算法前置）
    │  osrm-customize → 计算边权重/自定义数据
    ▼
.osrm.* 处理完成的路由图文件（约 26 个文件，总计 ~1GB）
    │  osrm-routed    → 启动路由守护进程，提供 HTTP API
    ▼
路由服务就绪
```

## 部署流程

### 环境要求

- **操作系统**：Linux（CentOS 7+ / Ubuntu 18.04+）
- **Docker**：20.10+
- **Python**：3.8+
- **内存**：至少 8GB（OSRM 容器需要约 4GB）
- **磁盘**：至少 5GB 可用空间（地图数据约 1GB + Docker 镜像约 500MB）

### 第一步：安装 Docker

```bash
# CentOS
sudo yum install -y yum-utils
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo yum install -y docker-ce docker-ce-cli containerd.io
sudo systemctl start docker
sudo systemctl enable docker

# Ubuntu
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io
```

验证安装：

```bash
docker --version
```

### 第二步：获取地图数据

从 Geofabrik 下载广东省地图数据：

```bash
cd /home/hadoop/Shenzhen_map_tool/OSRM_Tool
mkdir -p map_data
wget -O map_data/guangdong-latest.osm.pbf https://download.geofabrik.de/asia/china/guangdong-latest.osm.pbf
```

> 文件约 160MB，下载时间取决于网络状况。如果下载缓慢，也可以从其他镜像源获取 `.osm.pbf` 格式的地图文件后放入 `map_data/` 目录。

### 第三步：一键部署 OSRM 服务

项目提供了自动化部署脚本 `deploy_osrm.sh`（约 300 行），执行以下操作：

1. 配置 Docker 国内镜像加速（USTC、DaoCloud 等）
2. 拉取 `osrm/osrm-backend:latest` 镜像
3. 运行地图预处理三阶段：`osrm-extract` → `osrm-partition` → `osrm-customize`
4. 启动 `osrm-routed` 路由服务（容器名 `osrm-guangdong`，端口 5000）
5. 发送测试请求验证服务可用

```bash
cd /home/hadoop/Shenzhen_map_tool/OSRM_Tool
bash deploy_osrm.sh
```

> 地图预处理约需 5-10 分钟，请耐心等待。

如果不想使用一键脚本，也可以手动执行各步骤：

<details>
<summary>手动部署步骤（展开查看）</summary>

```bash
# 1. 拉取镜像
docker pull osrm/osrm-backend:latest

# 2. 地图预处理
docker run --rm -t -v $(pwd)/map_data:/data \
    osrm/osrm-backend:latest osrm-extract -p /opt/car.lua \
    /data/guangdong-latest.osm.pbf

docker run --rm -t -v $(pwd)/map_data:/data \
    osrm/osrm-backend:latest osrm-partition \
    /data/guangdong-latest.osrm

docker run --rm -t -v $(pwd)/map_data:/data \
    osrm/osrm-backend:latest osrm-customize \
    /data/guangdong-latest.osrm

# 3. 启动路由服务
docker run -d --name osrm-guangdong -p 5000:5000 \
    --memory=4g --restart unless-stopped \
    -v $(pwd)/map_data:/data \
    osrm/osrm-backend:latest \
    osrm-routed --algorithm mld /data/guangdong-latest.osrm

# 4. 测试服务
curl "http://localhost:5000/route/v1/driving/114.057868,22.543099;114.085947,22.547?overview=false"
```
</details>

部署成功后，输出类似：

```
OSRM service started ✓

Test route: Futian Station -> Luohu Station
  Distance: 4.12 km
  Duration: 12.3 min
```

### 第四步：安装 Python 依赖

```bash
cd /home/hadoop/Shenzhen_map_tool/OSRM_Tool
pip install -r requirements.txt
```

### 第五步：运行距离计算（两步）

计算分两步进行，中间文件可独立校验：

```bash
# 步骤 1：栈算法严格提取配对行程
python3 trip_pair_filter.py taxi_trip_clean.csv
# 输出 → taxi_trip_clean_paired_trips.csv

# 步骤 2：调用 OSRM 计算每段行程距离
python3 trip_osrm_distance.py taxi_trip_clean_paired_trips.csv --workers 10
# 输出 → taxi_trip_clean_paired_trips_trip_distances.csv
```

参数说明：

**trip_pair_filter.py**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `csv_file` | （必填） | 出租车 GPS 轨迹 CSV 文件路径 |
| `--output` | 自动生成 | 输出 CSV 文件名 |

**trip_osrm_distance.py**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `csv_file` | （必填） | 配对行程 CSV 文件路径 |
| `--host` | `localhost` | OSRM 服务地址 |
| `--port` | `5000` | OSRM 服务端口 |
| `--output` | 自动生成 | 输出 CSV 文件名 |
| `--workers` | `5` | 并发线程数 |

### 第六步：验证配对结果（可选）

```bash
# 验证距离文件中的配对是否与栈算法预期一致
python3 validate_trip_pairing.py
```

### 服务管理

```bash
docker ps                        # 查看 OSRM 容器状态
docker logs osrm-guangdong       # 查看 OSRM 日志
docker stop osrm-guangdong       # 停止服务
docker start osrm-guangdong      # 启动服务
docker restart osrm-guangdong    # 重启服务
```

## 输入输出说明

### 输入格式（taxi_trip_clean.csv）

| 字段 | 说明 |
|------|------|
| `VehicleNum` | 车辆编号 |
| `Stime` | 记录时间 |
| `Lng` | 经度（WGS-84） |
| `Lat` | 纬度（WGS-84） |
| `OpenStatus` | 载客状态（0=空车，1=载客） |
| `Speed` | 瞬时速度 |

### 中间输出格式（paired_trips.csv）

| 字段 | 说明 |
|------|------|
| `trip_id` | 行程编号 |
| `VehicleNum` | 车辆编号 |
| `start_time` / `end_time` | 行程起止时间 |
| `start_lng` / `start_lat` | 起点经纬度（上车点） |
| `end_lng` / `end_lat` | 终点经纬度（下车点） |

### 最终输出格式（trip_distances.csv）

| 字段 | 说明 |
|------|------|
| `trip_id` | 行程编号 |
| `VehicleNum` | 车辆编号 |
| `start_time` / `end_time` | 行程起止时间 |
| `start_lng` / `start_lat` | 起点经纬度 |
| `end_lng` / `end_lat` | 终点经纬度 |
| `distance_m` / `distance_km` | 驾车距离（米 / 公里） |
| `duration_s` / `duration_min` | 预估耗时（秒 / 分钟） |
| `success` | 是否计算成功 |
| `message` | 计算结果说明 |

## 项目文件说明

| 文件 | 说明 |
|------|------|
| `trip_pair_filter.py` | 第一步：栈算法严格提取配对行程，输出配对 CSV |
| `trip_osrm_distance.py` | 第二步：读取配对 CSV，调用 OSRM 计算驾车距离 |
| `validate_trip_pairing.py` | 验证脚本：用栈算法校验距离文件的配对正确性 |
| `taxi_trip_distance.py` | 旧版脚本（一步完成，有配对 bug，保留作为参考） |
| `deploy_osrm.sh` | OSRM 一键部署脚本 |
| `requirements.txt` | Python 依赖 |
| `taxi_trip_clean.csv` | 输入数据：出租车 GPS 轨迹 |
| `taxi_trip_clean_paired_trips.csv` | 中间结果：严格配对后的行程起止点 |
| `taxi_trip_clean_paired_trips_trip_distances.csv` | 最终结果：含驾车距离的行程数据 |
| `map_data/` | 地图数据目录（.osm.pbf + 预处理后的 .osrm.* 文件） |

## FAQ

**Q: 容器启动失败，提示内存不足？**

调小 `--memory` 参数（如改为 `--memory=2g`），或在内存更大的机器上运行。

**Q: 计算时大量请求失败？**

检查 OSRM 服务是否正常运行：`docker ps | grep osrm-guangdong`，并查看日志 `docker logs osrm-guangdong`。

**Q: 地图数据需要是多大的范围？**

OSRM 需要覆盖所有轨迹起点和终点的区域。如果轨迹只分布在深圳，广东省数据即可满足；如果跨省，需下载对应的 OSM 数据。
