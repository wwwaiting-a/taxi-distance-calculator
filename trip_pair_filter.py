"""
行程配对提取器 (栈算法)
========================

功能：从出租车GPS轨迹数据中，使用栈算法严格提取上下车配对行程。

核心逻辑：
  - 遍历每辆车的GPS记录，按时间排序
  - OpenStatus 从 0→1 变换时：入栈（上车点）
  - OpenStatus 从 1→0 变换时：出栈，与最近的入栈记录配对（下车点）
  - 若第一条记录 OpenStatus=1，说明数据窗口内该车已处于载客状态，
    初始载客段无上车记录，忽略

输出：包含起终点坐标的配对行程 CSV，后续由 trip_osrm_distance.py 计算距离。

用法：
  python trip_pair_filter.py taxi_trip_clean.csv [--output paired_trips.csv]
"""

import pandas as pd
import argparse
import sys
from collections import defaultdict


def extract_trips_stack(df: pd.DataFrame) -> pd.DataFrame:
    """
    使用栈算法严格提取行程配对。

    - 0→1: push 上车点
    - 1→0: pop 并配对待下车点
    - 第一条记录为 1: 不 push（起始上车点未知）
    """
    trips = []

    # 按车辆和时间排序
    df = df.sort_values(['VehicleNum', 'Stime']).reset_index(drop=True)

    for vehicle in df['VehicleNum'].unique():
        vehicle_df = df[df['VehicleNum'] == vehicle].copy()

        records = vehicle_df[['Stime', 'Lng', 'Lat', 'OpenStatus']].values.tolist()
        stack = []
        prev_status = records[0][3]
        trip_num = 0

        for i, rec in enumerate(records):
            stime, lng, lat, status = rec

            if i == 0:
                # 第一条记录：若为 1，不 push（起始上车点未知）
                pass
            else:
                if prev_status == 0 and status == 1:
                    # 0→1 上车点
                    stack.append(rec)
                elif prev_status == 1 and status == 0:
                    # 1→0 下车点
                    if stack:
                        trip_num += 1
                        pickup = stack.pop()
                        trips.append({
                            'trip_id': f"{vehicle}_{trip_num}",
                            'VehicleNum': vehicle,
                            'start_time': pickup[0],
                            'start_lng': pickup[1],
                            'start_lat': pickup[2],
                            'end_time': stime,
                            'end_lng': lng,
                            'end_lat': lat,
                        })

            prev_status = status

    result_df = pd.DataFrame(trips)
    if not result_df.empty:
        result_df = result_df[['trip_id', 'VehicleNum',
                                'start_time', 'start_lng', 'start_lat',
                                'end_time', 'end_lng', 'end_lat']]
    return result_df


def main():
    parser = argparse.ArgumentParser(description="行程配对提取器 (栈算法)")
    parser.add_argument("csv_file", help="出租车轨迹CSV文件 (如 taxi_trip_clean.csv)")
    parser.add_argument("--output", default=None,
                        help="输出文件名 (默认: 原文件名_paired_trips.csv)")
    args = parser.parse_args()

    # 读取数据
    print(f"读取文件: {args.csv_file}")
    df = pd.read_csv(args.csv_file)
    print(f"总记录数: {len(df)}, 车辆数: {df['VehicleNum'].nunique()}")

    # 检查起始状态分布
    df_sorted = df.sort_values(['VehicleNum', 'Stime'])
    first_records = df_sorted.groupby('VehicleNum').first()
    starts_with_1 = (first_records['OpenStatus'] == 1).sum()
    starts_with_0 = (first_records['OpenStatus'] == 0).sum()
    print(f"起始状态为 0 (空车): {starts_with_0} 辆")
    print(f"起始状态为 1 (载客): {starts_with_1} 辆")

    # 提取行程
    print("\n提取行程 (栈算法)...")
    trips_df = extract_trips_stack(df)
    print(f"提取到 {len(trips_df)} 段完整行程")

    if len(trips_df) == 0:
        print("未提取到任何行程，退出。")
        sys.exit(0)

    # 统计
    vehicles_with_trips = trips_df['VehicleNum'].nunique()
    print(f"涉及 {vehicles_with_trips} 辆车")
    print(f"平均每车 {len(trips_df) / vehicles_with_trips:.1f} 段行程")

    # 保存结果
    output_file = args.output or args.csv_file.replace('.csv', '_paired_trips.csv')
    trips_df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n配对行程已保存: {output_file}")


if __name__ == "__main__":
    main()
