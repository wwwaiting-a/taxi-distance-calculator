"""
出租车行程距离计算器
===================

功能说明：
1. 读取出租车GPS轨迹数据（taxi_trip_clean.csv）
2. 提取完整行程（OpenStatus 0→1 起点，1→0 终点）
3. 调用本地OSRM服务计算每段行程的驾车距离
4. 输出结果到CSV文件

"""

import pandas as pd
import requests
import argparse
import sys
import time
from typing import Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed


class OSRMClient:
    """OSRM"""
    
    def __init__(self, host: str = "localhost", port: int = 5000):
        self.base_url = f"http://{host}:{port}"
        self.session = requests.Session()
    
    def calculate_distance(
        self,
        origin_lng: float,
        origin_lat: float,
        dest_lng: float,
        dest_lat: float
    ) -> Dict:
        """计算两点之间的驾车距离"""
        coordinates = f"{origin_lng},{origin_lat};{dest_lng},{dest_lat}"
        url = f"{self.base_url}/route/v1/driving/{coordinates}"
        
        params = {
            "overview": "false",
            "alternatives": "false",
            "steps": "false"
        }
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get("code") != "Ok":
                return {
                    'success': False,
                    'distance': 0,
                    'duration': 0,
                    'message': f"OSRM错误：{data.get('message', '未知')}"
                }
            
            route = data['routes'][0]
            return {
                'success': True,
                'distance': route['distance'],
                'duration': route['duration'],
                'message': '成功'
            }
            
        except requests.exceptions.Timeout:
            return {'success': False, 'distance': 0, 'duration': 0, 'message': '请求超时'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'distance': 0, 'duration': 0, 'message': '无法连接OSRM'}
        except Exception as e:
            return {'success': False, 'distance': 0, 'duration': 0, 'message': str(e)}


def extract_trips(df: pd.DataFrame) -> pd.DataFrame:
    """
    提取完整行程
    
    OpenStatus变化规则：
    - 0 → 1: 乘客上车（起点）
    - 1 → 0: 乘客下车（终点）
    
    返回包含起终点坐标的DataFrame
    """
    trips = []
    
    # 按车辆和时间排序
    df = df.sort_values(['VehicleNum', 'Stime']).reset_index(drop=True)
    
    # 计算每个车辆的状态变化
    for vehicle in df['VehicleNum'].unique():
        vehicle_df = df[df['VehicleNum'] == vehicle].copy()
        vehicle_df['prev_status'] = vehicle_df['OpenStatus'].shift(1)
        
        # 找起点（prev_status=0, OpenStatus=1）
        starts = vehicle_df[(vehicle_df['prev_status'] == 0) & (vehicle_df['OpenStatus'] == 1)]
        
        # 找终点（prev_status=1, OpenStatus=0）
        ends = vehicle_df[(vehicle_df['prev_status'] == 1) & (vehicle_df['OpenStatus'] == 0)]
        
        # 匹配起点和终点（按时间顺序）
        start_list = starts[['Stime', 'Lng', 'Lat']].values.tolist()
        end_list = ends[['Stime', 'Lng', 'Lat']].values.tolist()
        
        for i, start in enumerate(start_list):
            if i < len(end_list):
                end = end_list[i]
                trips.append({
                    'VehicleNum': vehicle,
                    'trip_id': f"{vehicle}_{i+1}",
                    'start_time': start[0],
                    'start_lng': start[1],
                    'start_lat': start[2],
                    'end_time': end[0],
                    'end_lng': end[1],
                    'end_lat': end[2]
                })
    
    return pd.DataFrame(trips)


def calculate_trip_distances(
    trips_df: pd.DataFrame,
    osrm_client: OSRMClient,
    batch_size: int = 100,
    max_workers: int = 5
) -> pd.DataFrame:
    """
    批量计算行程距离（多线程）
    """
    results = []
    total = len(trips_df)
    
    print(f"\n开始计算 {total} 段行程距离...")
    
    def calculate_single(row):
        result = osrm_client.calculate_distance(
            row['start_lng'], row['start_lat'],
            row['end_lng'], row['end_lat']
        )
        return {
            'trip_id': row['trip_id'],
            'VehicleNum': row['VehicleNum'],
            'start_time': row['start_time'],
            'start_lng': row['start_lng'],
            'start_lat': row['start_lat'],
            'end_time': row['end_time'],
            'end_lng': row['end_lng'],
            'end_lat': row['end_lat'],
            'distance_m': result['distance'],
            'distance_km': result['distance'] / 1000,
            'duration_s': result['duration'],
            'duration_min': result['duration'] / 60,
            'success': result['success'],
            'message': result['message']
        }
    
    # 使用多线程加速
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(calculate_single, row): idx for idx, row in trips_df.iterrows()}
        
        completed = 0
        for future in as_completed(futures):
            results.append(future.result())
            completed += 1
            
            if completed % 100 == 0 or completed == total:
                print(f"已完成: {completed}/{total} ({completed*100//total}%)")
    
    return pd.DataFrame(results)


def main():
    parser = argparse.ArgumentParser(description="出租车行程距离计算器")
    
    parser.add_argument("csv_file", help="出租车轨迹CSV文件")
    parser.add_argument("--host", default="localhost", help="OSRM服务地址")
    parser.add_argument("--port", type=int, default=5000, help="OSRM服务端口")
    parser.add_argument("--output", default=None, help="输出文件名（默认：原文件名_trip_distances.csv）")
    parser.add_argument("--workers", type=int, default=5, help="并发线程数")
    
    args = parser.parse_args()
    
    # 读取数据
    print(f"读取文件: {args.csv_file}")
    df = pd.read_csv(args.csv_file)
    print(f"总记录数: {len(df)}, 车辆数: {df['VehicleNum'].nunique()}")
    
    # 提取行程
    print("\n提取行程...")
    trips_df = extract_trips(df)
    print(f"提取到 {len(trips_df)} 段完整行程")
    
    # 初始化OSRM客户端
    osrm_client = OSRMClient(args.host, args.port)
    
    # 测试OSRM连接
    print(f"\n测试OSRM服务 ({args.host}:{args.port})...")
    test_result = osrm_client.calculate_distance(114.057, 22.543, 114.085, 22.547)
    if test_result['success']:
        print("✓ OSRM服务正常")
    else:
        print(f"✗ OSRM服务异常: {test_result['message']}")
        print("请确保OSRM服务已启动: docker start osrm-guangdong")
        sys.exit(1)
    
    # 计算距离
    results_df = calculate_trip_distances(trips_df, osrm_client, max_workers=args.workers)
    
    # 统计结果
    print(f"\n{'='*50}")
    print("计算完成！统计结果：")
    print(f"{'='*50}")
    print(f"成功计算: {results_df['success'].sum()} 段")
    print(f"计算失败: {(~results_df['success']).sum()} 段")
    
    if results_df['success'].any():
        success_df = results_df[results_df['success']]
        print(f"\n距离统计:")
        print(f"  平均距离: {success_df['distance_km'].mean():.2f} 公里")
        print(f"  最短距离: {success_df['distance_km'].min():.2f} 公里")
        print(f"  最长距离: {success_df['distance_km'].max():.2f} 公里")
        print(f"  总距离: {success_df['distance_km'].sum():.2f} 公里")
        print(f"\n时间统计:")
        print(f"  平均时长: {success_df['duration_min'].mean():.1f} 分钟")
        print(f"  最短时长: {success_df['duration_min'].min():.1f} 分钟")
        print(f"  最长时长: {success_df['duration_min'].max():.1f} 分钟")
    
    # 保存结果
    output_file = args.output or args.csv_file.replace('.csv', '_trip_distances.csv')
    results_df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存: {output_file}")


if __name__ == "__main__":
    main()