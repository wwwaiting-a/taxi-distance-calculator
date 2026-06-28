"""
行程距离计算器
==============

功能：读取 trip_pair_filter.py 生成的配对行程 CSV，调用本地 OSRM 服务计算驾车距离。

前置条件：
  1. 已运行 trip_pair_filter.py 生成配对行程文件
  2. OSRM 服务已启动 (如 docker start osrm-guangdong)

用法：
  python trip_osrm_distance.py paired_trips.csv [--host localhost] [--port 5000] [--workers 5]
"""

import pandas as pd
import requests
import argparse
import sys
from typing import Dict
from concurrent.futures import ThreadPoolExecutor, as_completed


class OSRMClient:
    """OSRM 路由服务客户端"""

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
        """计算两点之间的驾车距离和时长"""
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
                    'message': f"OSRM错误: {data.get('message', '未知')}"
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


def calculate_trip_distances(
    trips_df: pd.DataFrame,
    osrm_client: OSRMClient,
    max_workers: int = 5
) -> pd.DataFrame:
    """批量计算行程距离（多线程）"""
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

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(calculate_single, row): idx
                   for idx, row in trips_df.iterrows()}

        completed = 0
        for future in as_completed(futures):
            results.append(future.result())
            completed += 1

            if completed % 100 == 0 or completed == total:
                print(f"  已完成: {completed}/{total} ({completed*100//total}%)")

    return pd.DataFrame(results)


def main():
    parser = argparse.ArgumentParser(description="行程距离计算器")
    parser.add_argument("csv_file", help="配对行程CSV文件 (trip_pair_filter.py 的输出)")
    parser.add_argument("--host", default="localhost", help="OSRM服务地址")
    parser.add_argument("--port", type=int, default=5000, help="OSRM服务端口")
    parser.add_argument("--output", default=None,
                        help="输出文件名 (默认: 原文件名_trip_distances.csv)")
    parser.add_argument("--workers", type=int, default=5, help="并发线程数")

    args = parser.parse_args()

    # 读取数据
    print(f"读取文件: {args.csv_file}")
    trips_df = pd.read_csv(args.csv_file)
    required_cols = ['trip_id', 'VehicleNum',
                     'start_time', 'start_lng', 'start_lat',
                     'end_time', 'end_lng', 'end_lat']
    missing = [c for c in required_cols if c not in trips_df.columns]
    if missing:
        print(f"错误: 输入文件缺少必要列: {missing}")
        print(f"请确保使用 trip_pair_filter.py 生成的配对行程文件作为输入")
        sys.exit(1)

    print(f"行程数: {len(trips_df)}, 车辆数: {trips_df['VehicleNum'].nunique()}")

    # 初始化 OSRM 客户端
    osrm_client = OSRMClient(args.host, args.port)

    # 测试连接
    print(f"\n测试 OSRM 服务 ({args.host}:{args.port})...")
    test_result = osrm_client.calculate_distance(114.057, 22.543, 114.085, 22.547)
    if test_result['success']:
        print("  OSRM 服务正常")
    else:
        print(f"  OSRM 服务异常: {test_result['message']}")
        print("  请确保 OSRM 服务已启动: docker start osrm-guangdong")
        sys.exit(1)

    # 计算距离
    results_df = calculate_trip_distances(trips_df, osrm_client, max_workers=args.workers)

    # 统计
    print(f"\n{'='*50}")
    print("计算完成！统计结果：")
    print(f"{'='*50}")
    n_success = results_df['success'].sum()
    print(f"成功: {n_success} 段")
    print(f"失败: {(~results_df['success']).sum()} 段")

    if n_success > 0:
        success_df = results_df[results_df['success']]
        print(f"\n距离统计:")
        print(f"  平均: {success_df['distance_km'].mean():.2f} km")
        print(f"  最短: {success_df['distance_km'].min():.2f} km")
        print(f"  最长: {success_df['distance_km'].max():.2f} km")
        print(f"  合计: {success_df['distance_km'].sum():.2f} km")
        print(f"\n时长统计:")
        print(f"  平均: {success_df['duration_min'].mean():.1f} min")
        print(f"  最短: {success_df['duration_min'].min():.1f} min")
        print(f"  最长: {success_df['duration_min'].max():.1f} min")

    # 保存
    output_file = args.output or args.csv_file.replace('.csv', '_trip_distances.csv')
    results_df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存: {output_file}")


if __name__ == "__main__":
    main()
