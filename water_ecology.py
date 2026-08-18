"""กฎที่ผูก "น้ำไหลแรงแค่ไหน" เข้ากับสิ่งที่ผู้เล่นเห็นในน้ำ — แหล่งความจริงเดียว

ทำไมต้องมีไฟล์นี้: ก่อนหน้านี้ทุก cell ของลำน้ำได้วัสดุก้นชุดเดียวกัน (สุ่มจาก
hash: ดิน/ดินหยาบ/กรวด/มอส/พอดโซล/ดินเหนียว) ไม่ว่าจะเป็นแก่งบนที่ชัน 30% หรือ
ลำธารเอื่อยกลางทุ่ง — ก้นดินกับพอดโซลใต้แก่งคือสิ่งที่ตาอ่านออกทันทีว่า "ไม่จริง"
เพราะน้ำเชี่ยวพัดตะกอนละเอียดออกหมดจนเหลือแต่หินกับกรวด (bed armouring)

`flow_index` มาจาก `channel_sections.flow_index` = **ความชันของลำน้ำต่อพัน**
(0..255 = 0..25.5%) วัดบนหน้าต่าง ±16 บล็อก  ชั้นข้างล่างตั้งตามค่าที่ใช้จำแนก
ลำน้ำจริงในภูมิศาสตร์ ไม่ใช่ตัวเลขกลม ๆ ที่เลือกให้กราฟสวย:

    นิ่ง   <=5    แม่น้ำในหุบ ทะเลสาบ ปากน้ำ      -> ตะกอนละเอียดสะสมได้
    เอื่อย 6-20   ลำน้ำบนที่ราบเชิงเขา            -> ทราย/กรวด
    เชี่ยว 21-60  ลำธารไหล่เขาทั่วไป              -> กรวด/หินก้อน
    แรง    >60    ธารบนที่ชัน แก่ง ใต้น้ำตก        -> หินก้อน/หินโผล่ ไม่มีตะกอน
"""

import numpy as np

# ---- ชั้นของแรงน้ำ ----
STILL_MAX = 5
SLACK_MAX = 20
BRISK_MAX = 60

FLOW_NAMES = ("still", "slack", "brisk", "fast")
STILL, SLACK, BRISK, FAST = range(4)

# ---- วัสดุก้นน้ำ ----
# ชุดเดียวกับที่ `paint_surface` ใช้ทั้งทะเลสาบและลำน้ำ ย้ายมาไว้ที่นี่เพราะ
# ตอนนี้มีผู้ตัดสินสองที่ (ทะเลสาบตัดสินจากความลึก ลำน้ำตัดสินจากแรงน้ำ) และ
# ตารางต้องเป็นตัวเดียวกัน ไม่งั้นรหัสเดียวกันจะหมายถึงคนละบล็อก
LAKEBED_NAMES = (
    "stream", "sand", "gravel", "clay", "mud",
    "stone", "cobble", "calcite",
)
LAKEBED = {name: i for i, name in enumerate(LAKEBED_NAMES)}


def flow_class(flow_index):
    """แปลงดัชนี 0..255 เป็นชั้น 0..3"""
    flow = np.asarray(flow_index, dtype=np.int32)
    out = np.zeros(flow.shape, dtype=np.uint8)
    out[flow > STILL_MAX] = SLACK
    out[flow > SLACK_MAX] = BRISK
    out[flow > BRISK_MAX] = FAST
    return out


def streambed_materials(flow_index, depth, water, pool_mask=None,
                        texture=None):
    """วัสดุก้นลำน้ำจากแรงน้ำ — คืนรหัสชุดเดียวกับ `LAKEBED`

    หลักการเดียว: **ตะกอนที่ละเอียดกว่าที่แรงน้ำจะพัดไปได้เท่านั้นที่อยู่ได้**
    น้ำแรงจึงเหลือหินกับกรวด ส่วนแอ่งนิ่งเก็บทราย/ดินเหนียวไว้ได้

    ``texture`` คือ noise 0..1 ที่ผู้เรียกส่งเข้ามา (ใช้ตัวเดียวกับก้นทะเลสาบ
    เพื่อให้ลายต่อเนื่องกันตรงที่ลำน้ำไหลลงทะเลสาบ) ถ้าไม่ส่งจะได้ลายคงที่
    ซึ่งใช้ได้ในเทสต์แต่จะเป็นแถบเรียบเกินไปในโลกจริง
    """
    flow = np.asarray(flow_index, dtype=np.int32)
    depth = np.asarray(depth, dtype=np.int32)
    water = np.asarray(water, dtype=bool)
    if flow.shape != depth.shape or flow.shape != water.shape:
        raise ValueError("flow_index, depth, water ต้องรูปร่างเท่ากัน")
    texture = (
        np.full(flow.shape, 0.5, dtype=np.float32) if texture is None
        else np.clip(np.asarray(texture, dtype=np.float32), 0.0, 0.999)
    )
    pool = (
        np.zeros(flow.shape, dtype=bool) if pool_mask is None
        else np.asarray(pool_mask, dtype=bool)
    )

    kind = flow_class(flow)
    out = np.full(flow.shape, LAKEBED["gravel"], dtype=np.uint8)

    # นิ่ง: ตะกอนละเอียดสะสมได้ — แต่ยังไม่ใช่ทะเลสาบ จึงมีกรวดปนเป็นหลัก
    still = water & (kind == STILL)
    out[still & (texture < 0.40)] = LAKEBED["gravel"]
    out[still & (texture >= 0.40) & (texture < 0.70)] = LAKEBED["sand"]
    out[still & (texture >= 0.70)] = LAKEBED["clay"]
    # แอ่งลึกในช่วงนิ่งคือที่เดียวที่โคลนอยู่ได้
    out[still & (depth >= 3) & (texture >= 0.82)] = LAKEBED["mud"]

    slack = water & (kind == SLACK)
    out[slack & (texture < 0.55)] = LAKEBED["gravel"]
    out[slack & (texture >= 0.55) & (texture < 0.85)] = LAKEBED["sand"]
    out[slack & (texture >= 0.85)] = LAKEBED["cobble"]

    brisk = water & (kind == BRISK)
    out[brisk & (texture < 0.45)] = LAKEBED["gravel"]
    out[brisk & (texture >= 0.45) & (texture < 0.88)] = LAKEBED["cobble"]
    out[brisk & (texture >= 0.88)] = LAKEBED["stone"]

    # แรง: ไม่มีทราย ไม่มีดินเหนียว ไม่มีโคลน — หินโผล่กับหินก้อนล้วน
    fast = water & (kind == FAST)
    out[fast & (texture < 0.55)] = LAKEBED["cobble"]
    out[fast & (texture >= 0.55)] = LAKEBED["stone"]

    # แอ่งใต้น้ำตกถูกน้ำตกครูดตลอดเวลา ตะกอนละเอียดอยู่ไม่ได้แม้ผิวน้ำจะนิ่ง
    out[water & pool & (texture < 0.6)] = LAKEBED["gravel"]
    out[water & pool & (texture >= 0.6)] = LAKEBED["cobble"]

    out[~water] = LAKEBED["stream"]
    return out


def submerged_plants_allowed(flow_index):
    """หญ้าน้ำยึดพื้นได้เฉพาะที่กระแสไม่พัดหลุด"""
    return np.asarray(flow_index, dtype=np.int32) <= SLACK_MAX


def floating_plants_allowed(flow_index):
    """ใบบัวลอยอยู่ได้เฉพาะน้ำนิ่งจริง ๆ — ในลำธารมันจะถูกพัดไปแล้ว"""
    return np.asarray(flow_index, dtype=np.int32) <= STILL_MAX


def reeds_allowed(flow_index):
    """กกขึ้นบนตลิ่งที่น้ำข้าง ๆ ไม่เชี่ยว ตลิ่งของแก่งเป็นหินเปล่า"""
    return np.asarray(flow_index, dtype=np.int32) <= SLACK_MAX


def spray_zone(flow_index, threshold=BRISK_MAX):
    """ละอองน้ำ — ตลิ่งข้างน้ำแรงชื้นตลอด จึงเป็นที่ของมอส

    ใช้กับ cell *น้ำ* ผู้เรียกเป็นคนแผ่ไปยังตลิ่งข้างเคียงเอง เพราะรู้ว่า
    ตลิ่งของตัวเองอยู่ตรงไหน
    """
    return np.asarray(flow_index, dtype=np.int32) > threshold


def histogram(flow_index, water):
    """การกระจายของแรงน้ำ — ใช้ตั้งเกณฑ์จากข้อมูลจริง ไม่ใช่จากความรู้สึก"""
    flow = np.asarray(flow_index, dtype=np.int32)[np.asarray(water, dtype=bool)]
    if flow.size == 0:
        return {}
    kinds = flow_class(flow)
    return {
        "cells": int(flow.size),
        "p50": int(np.percentile(flow, 50)),
        "p90": int(np.percentile(flow, 90)),
        "p99": int(np.percentile(flow, 99)),
        "share": {
            name: float((kinds == i).mean())
            for i, name in enumerate(FLOW_NAMES)
        },
    }
