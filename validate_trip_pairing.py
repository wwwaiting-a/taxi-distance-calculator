#!/usr/bin/env python3
"""
验证 taxi_trip_clean_trip_distances.csv 中的配对结果是否符合
taxi_trip_clean.csv 中的上下车状态转换。

核心逻辑（栈思想）：
  - 遍历每辆车的GPS记录，按时间排序
  - OpenStatus 从 0→1 变换时：入栈（上车点，trip start）
  - OpenStatus 从 1→0 变换时：出栈，与最近的入栈记录配对（下车点，trip end）
  - 若第一条记录 OpenStatus=1，说明数据窗口内该车已处于载客状态，
    起始上车点不可知，因此不纳入配对
  - 由此生成所有 trip 配对，与 distances 文件逐一比对
"""

import csv
import sys
from collections import defaultdict


def parse_source(filepath):
    """
    解析 taxi_trip_clean.csv，按车辆分组、按时间排序。
    返回: {VehicleNum: [{"time", "lng", "lat", "status"}, ...]}
    """
    vehicle_records = defaultdict(list)
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            vehicle_records[row['VehicleNum']].append({
                'time': row['Stime'],
                'lng': float(row['Lng']),
                'lat': float(row['Lat']),
                'status': int(row['OpenStatus']),
            })

    for vid in vehicle_records:
        vehicle_records[vid].sort(key=lambda r: r['time'])

    return vehicle_records


def extract_trips_from_source(vehicle_records):
    """
    使用栈思想从源数据中提取 trip 配对。
    - 0→1 转换: push 上车点
    - 1→0 转换: pop 并配对下车点
    - 若车辆起始状态为 1，初始载客段忽略（无 0→1 转换记录）
    - 若 1→0 时栈为空，说明该下车无对应上车记录（起始状态为1的车辆），忽略

    返回: {
        (VehicleNum, start_time): {
            "start_time", "start_lng", "start_lat",
            "end_time", "end_lng", "end_lat"
        }, ...
    }
    """
    trips = {}
    stats = {
        'total_vehicles': len(vehicle_records),
        'starts_with_0': 0,
        'starts_with_1': 0,
        'orphan_dropoffs': 0,        # 1→0 时栈为空
        'unmatched_pickups': 0,       # 最终栈非空
    }

    for vid, records in vehicle_records.items():
        if not records:
            continue

        first_status = records[0]['status']
        if first_status == 0:
            stats['starts_with_0'] += 1
        else:
            stats['starts_with_1'] += 1

        stack = []
        prev_status = records[0]['status']

        for i, rec in enumerate(records):
            curr_status = rec['status']

            if i == 0:
                # 第一条记录：若为 1，不 push（起始上车点未知）
                # 若为 0，保持空栈
                pass
            else:
                if prev_status == 0 and curr_status == 1:
                    # 0→1 上车点
                    stack.append(rec)
                elif prev_status == 1 and curr_status == 0:
                    # 1→0 下车点
                    if not stack:
                        stats['orphan_dropoffs'] += 1
                        print(f"[INFO] 车辆 {vid}: 起始状态为 1（载客中），"
                              f"首个下车点 {rec['time']} 无可配对上车点，已跳过")
                    else:
                        pickup = stack.pop()
                        key = (vid, pickup['time'])
                        trips[key] = {
                            'start_time': pickup['time'],
                            'start_lng': pickup['lng'],
                            'start_lat': pickup['lat'],
                            'end_time': rec['time'],
                            'end_lng': rec['lng'],
                            'end_lat': rec['lat'],
                        }

            prev_status = curr_status

        # 最终栈中剩余的上车点（最后一段行程未完成）
        if stack:
            stats['unmatched_pickups'] += len(stack)
            for pickup in stack:
                print(f"[WARN] 车辆 {vid}: 上车点 {pickup['time']} 无对应下车记录，未配对")

    return trips, stats


def parse_distances(filepath):
    """
    解析 taxi_trip_clean_trip_distances.csv。
    返回: {(VehicleNum, start_time): {"trip_id", "start_time", "start_lng",
           "start_lat", "end_time", "end_lng", "end_lat"}, ...}
    """
    dist_trips = {}
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row['VehicleNum'], row['start_time'])
            dist_trips[key] = {
                'trip_id': row['trip_id'],
                'start_time': row['start_time'],
                'start_lng': float(row['start_lng']),
                'start_lat': float(row['start_lat']),
                'end_time': row['end_time'],
                'end_lng': float(row['end_lng']),
                'end_lat': float(row['end_lat']),
            }
    return dist_trips


def validate(source_trips, dist_trips, stats):
    """
    双向比对源数据生成的配对与 distances 文件中的配对。
    """
    source_keys = set(source_trips.keys())
    dist_keys = set(dist_trips.keys())

    only_in_source = source_keys - dist_keys
    only_in_dist = dist_keys - source_keys
    common_keys = source_keys & dist_keys

    print()
    print("=" * 70)
    print("源数据概况")
    print("=" * 70)
    print(f"  总车辆数:           {stats['total_vehicles']}")
    print(f"  起始状态为 0 (空车): {stats['starts_with_0']}")
    print(f"  起始状态为 1 (载客): {stats['starts_with_1']}")
    print(f"  跳过无配对下车点:    {stats['orphan_dropoffs']}")
    print(f"  无下车记录的上车点:  {stats['unmatched_pickups']}")

    print()
    print("=" * 70)
    print("配对验证结果")
    print("=" * 70)
    print(f"  源数据(栈算法)生成 trip: {len(source_trips)}")
    print(f"  distances 文件 trip:     {len(dist_trips)}")
    print(f"  上车时间匹配的 trip:     {len(common_keys)}")
    print(f"  仅在源数据中存在:        {len(only_in_source)}")
    print(f"  仅在 distances 中存在:   {len(only_in_dist)}")

    # 仅在源数据中存在
    if only_in_source:
        print()
        print(f"[仅在源数据中存在] {len(only_in_source)} 条 — distances 文件缺失这些 trip:")
        # 只展示前 20 条
        shown = sorted(only_in_source)[:20]
        for key in shown:
            t = source_trips[key]
            print(f"  车辆={key[0]}, 上车={t['start_time']}, 下车={t['end_time']}")
        if len(only_in_source) > 20:
            print(f"  ... 还有 {len(only_in_source) - 20} 条，省略")

    # 仅在 distances 中存在
    if only_in_dist:
        print()
        print(f"[仅在 distances 中存在] {len(only_in_dist)} 条 — 源数据无法配对:")
        shown = sorted(only_in_dist)[:20]
        for key in shown:
            t = dist_trips[key]
            print(f"  {t['trip_id']}: 车辆={key[0]}, 上车={t['start_time']}, 下车={t['end_time']}")
        if len(only_in_dist) > 20:
            print(f"  ... 还有 {len(only_in_dist) - 20} 条，省略")

    # 逐条比对公共部分
    mismatch_details = []
    for key in sorted(common_keys):
        s = source_trips[key]
        d = dist_trips[key]
        errors = []

        if s['end_time'] != d['end_time']:
            errors.append(f"下车时间: 期望={s['end_time']}, 实际={d['end_time']}")

        tol = 1e-6
        if abs(s['start_lng'] - d['start_lng']) > tol:
            errors.append(f"上车经度: 期望={s['start_lng']}, 实际={d['start_lng']}")
        if abs(s['start_lat'] - d['start_lat']) > tol:
            errors.append(f"上车纬度: 期望={s['start_lat']}, 实际={d['start_lat']}")
        if abs(s['end_lng'] - d['end_lng']) > tol:
            errors.append(f"下车经度: 期望={s['end_lng']}, 实际={d['end_lng']}")
        if abs(s['end_lat'] - d['end_lat']) > tol:
            errors.append(f"下车纬度: 期望={s['end_lat']}, 实际={d['end_lat']}")

        if errors:
            mismatch_details.append((d['trip_id'], key[0], errors))

    if mismatch_details:
        print()
        print(f"[字段不匹配] 上车时间相同但下车时间不一致: {len(mismatch_details)} 条")

        # 按车辆分组统计
        vehicle_mismatch_count = defaultdict(int)
        for _, vid, _ in mismatch_details:
            vehicle_mismatch_count[vid] += 1

        print(f"  涉及 {len(vehicle_mismatch_count)} 辆车，每辆车的不匹配 trip 数:")
        for vid in sorted(vehicle_mismatch_count.keys(), key=lambda v: -vehicle_mismatch_count[v]):
            print(f"    车辆 {vid}: {vehicle_mismatch_count[vid]} 条")

        # 展示前 10 条详情
        print(f"\n  前 10 条详情:")
        for trip_id, vid, errors in mismatch_details[:10]:
            print(f"    [{trip_id}] 车辆={vid}")
            for e in errors:
                print(f"      {e}")
        if len(mismatch_details) > 10:
            print(f"    ... 还有 {len(mismatch_details) - 10} 条")

    # 最终结论
    print()
    print("=" * 70)
    total_issues = len(only_in_source) + len(only_in_dist) + len(mismatch_details)
    if total_issues == 0:
        print("验证通过: distances 文件中的配对与源数据完全一致！")
        passed = True
    else:
        print(f"验证未通过: 共发现 {total_issues} 处差异")
        if len(only_in_source) > 0:
            print(f"  - {len(only_in_source)} 条 trip 在源数据中存在但 distances 文件缺失")
        if len(only_in_dist) > 0:
            print(f"  - {len(only_in_dist)} 条 trip 在 distances 文件中存在但源数据无法配对")
        if len(mismatch_details) > 0:
            print(f"  - {len(mismatch_details)} 条 trip 上车时间匹配但下车点不一致 (配对偏移)")
            print(f"    这通常对应起始状态为 1 的车辆，distances 文件的配对存在系统性偏移")
        passed = False
    print("=" * 70)

    return passed


def main():
    source_path = 'OSRM_Tool/taxi_trip_clean.csv'
    dist_path = 'OSRM_Tool/taxi_trip_clean_trip_distances.csv'

    print("解析源数据...")
    vehicle_records = parse_source(source_path)
    total_records = sum(len(v) for v in vehicle_records.values())
    print(f"  共 {len(vehicle_records)} 辆车, {total_records} 条 GPS 记录")

    print("从源数据提取 trip 配对 (栈算法)...")
    source_trips, stats = extract_trips_from_source(vehicle_records)
    print(f"  提取 {len(source_trips)} 个 trip")

    print("解析 distances 文件...")
    dist_trips = parse_distances(dist_path)
    print(f"  读取 {len(dist_trips)} 个 trip")

    passed = validate(source_trips, dist_trips, stats)
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
