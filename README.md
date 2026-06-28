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
[行程提取] 按车辆分组 → 检测乘客上下车事件
    │  OpenStatus: 0→1 乘客上车（起点）
    │  OpenStatus: 1→0 乘客下车（终点）
    │
    ▼
[距离计算] 起点/终点经纬度 → OSRM HTTP API
    │  GET /route/v1/driving/{lng},{lat};{lng},{lat}
    │  多线程并发请求
    │
    ▼
[结果输出] CSV 文件
       trip_id, 起终点坐标, 距离(km), 耗时(min)
```

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
cd /home/hadoop/program/map_ShenZhen/OSRM_Tool
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
cd /home/hadoop/program/map_ShenZhen/OSRM_Tool
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
cd /home/hadoop/program/map_ShenZhen/OSRM_Tool
pip install -r requirements.txt
```

### 第五步：运行距离计算

```bash
python3 taxi_trip_distance.py taxi_trip_clean.csv --workers 10
```

参数说明：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `csv_file` | （必填） | 出租车 GPS 轨迹 CSV 文件路径 |
| `--host` | `localhost` | OSRM 服务地址 |
| `--port` | `5000` | OSRM 服务端口 |
| `--output` | 自动生成 | 输出 CSV 文件名 |
| `--workers` | `5` | 并发线程数 |

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

### 输出格式（trip_distances.csv）

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
| `taxi_trip_distance.py` | 主程序：行程提取 + OSRM 距离计算 |
| `deploy_osrm.sh` | OSRM 一键部署脚本 |
| `requirements.txt` | Python 依赖 |
| `taxi_trip_clean.csv` | 示例输入：出租车 GPS 轨迹数据 |
| `map_data/` | 地图数据目录（.osm.pbf + 预处理后的 .osrm.* 文件） |

## FAQ

**Q: 容器启动失败，提示内存不足？**

调小 `--memory` 参数（如改为 `--memory=2g`），或在内存更大的机器上运行。

**Q: 计算时大量请求失败？**

检查 OSRM 服务是否正常运行：`docker ps | grep osrm-guangdong`，并查看日志 `docker logs osrm-guangdong`。

**Q: 地图数据需要是多大的范围？**

OSRM 需要覆盖所有轨迹起点和终点的区域。如果轨迹只分布在深圳，广东省数据即可满足；如果跨省，需下载对应的 OSM 数据。
