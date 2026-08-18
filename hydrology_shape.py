"""Prototype immersive river geometry from directed OSM centerlines.

This module intentionally works on patches first. It does not overwrite the
production terrain/water arrays until the geometry passes visual acceptance.
"""

import os
import sys
import heapq
import json

import numpy as np
from scipy import ndimage

import config as C
import surface as S
import channel_sections as CS
from pipeline_progress import use_utf8_stdout


HERE = os.path.dirname(os.path.abspath(__file__))
UNRESOLVED = np.iinfo(np.int16).min
MAX_WATERFALL_DROP = 4

# ---- การขึ้นรูปตลิ่ง ----
# แถวที่ต้องยกให้ถึงผิวน้ำจริง ๆ เพื่อกันน้ำรั่ว EDT ให้ระยะ 1.0 กับบล็อกที่ติด
# น้ำแบบ cardinal ซึ่งเป็นทิศเดียวที่ water spread ใน Minecraft
BANK_SEAL_BLOCKS = 1.0
# ระยะที่การขึ้นรูปตลิ่ง "จางหมด" พอดี — ที่ระยะนี้ terrain ต้องเท่าของเดิมเป๊ะ
# ต้องกว้างกว่า seal พอสมควร ไม่งั้นจะเห็นเป็นขอบคมเหมือนสูตรเดิม
BANK_BLEND_BLOCKS = 8.0
# เพดานการขยับตลิ่งทะเลสาบจาก DEM เดิม — ต่ำกว่าของลำธาร (max_channel_adjust=12)
# เพราะขอบทะเลสาบมีหน้าผาจริงเยอะ ถ้าปล่อยให้ลาดตลิ่งกดได้ไม่จำกัด หน้าผาสูง
# หลายสิบบล็อกริมทะเลสาบจะถูกไถทิ้งทั้งแถบ  4 พอสำหรับลบผนังตั้ง 1-2 บล็อก
MAX_LAKE_BANK_ADJUST = 4
# ความยาวของ "ขั้นบันได" บนตลิ่ง — ระดับน้ำที่ใช้เป็นเพดานการกดถูกมองเป็นค่าสูงสุด
# ในหน้าต่างนี้ ตลิ่งจึงราบเป็นช่วง ๆ แทนที่จะไล่ลงทีละบล็อกตามผิวน้ำ
BANK_TERRACE_BLOCKS = 5

# ---- แก่ง/ฝายหิน (ดู build_step_weirs) ----
# **ปิดอยู่** — ลองแล้วผลออกมาแย่กว่าเดิมชัดเจน
#
# ตัวเลขดีทุกตัว (จุดที่น้ำแผ่ทับบนผืนกว้าง 32,347 -> 12,992, ขั้นห่างขึ้นเท่าตัว)
# แต่ในเกมมันน่าเกลียด เพราะฟังก์ชันนี้ตั้งได้แค่ *ระดับ* ของสัน ไม่ได้บอก
# paint_surface ว่าต้องวางหิน  cell ที่ถูกเปลี่ยนเป็นบกจึงถูกทาด้วยวัสดุผิวปกติ
# ตามความสูง/ความชัน ซึ่งแถวนั้นคือ **หญ้า** — ได้แถบหญ้าขวางแม่น้ำแทนแก่งหิน
#
# ถ้าจะรื้อกลับมาต้องเพิ่ม product ใหม่ (เช่น `weir_mask`) ให้ paint_surface อ่าน
# แล้ววางหิน/กรวดตรงนั้นโดยเฉพาะ ไม่ใช่แก้แค่ในนี้
ENABLE_STEP_WEIRS = False
# เพื่อนบ้านที่เป็นน้ำขั้นต่ำที่ถือว่า "ลำน้ำกว้าง" — ลำธาร 1-2 บล็อกบนภูเขามีไม่
# ถึง 3 จึงไม่ถูกแตะ ขั้นของมันอ่านเป็นแก่งน้ำอยู่แล้ว (ผู้เล่นยืนยันว่าสวย)
WEIR_MIN_WET_NEIGHBOURS = 3
# เกณฑ์ noise ที่เปิดเป็นช่องน้ำไหลผ่านสันหิน (ค่าน้อย = ช่องเยอะ)
# ต้องเป็นค่าคงที่ ไม่ใช่ quantile ของหน้าต่าง ไม่งั้น --global เพี้ยนจาก --patch
WEIR_OPEN_SHARE = 0.10

# ความลึกสูงสุดของลำน้ำ (บล็อก) — ที่ 4 m/block เท่ากับ 16 m ซึ่งลึกกว่าแม่น้ำ
# จริงในพื้นที่นี้อยู่แล้ว แต่จำเป็นทางสายตา: น้ำตื้น 1-2 บล็อกมองทะลุเห็นท้อง
# น้ำจนไม่อ่านว่าเป็นแม่น้ำ  ทะเลสาบใช้เพดานคนละตัว (WATER_MAX_DEPTH_BLOCKS)
WATERWAY_MAX_DEPTH = 4

# ความกว้างหน้าตัดสูงสุดที่ยอมยุบให้ราบ (ดู flatten_cross_sections)
# ต้องกว้างกว่าลำน้ำที่กว้างที่สุดเล็กน้อย แต่แคบกว่าความยาวช่วงที่ลำน้ำวิ่ง
# ขนานแกน ไม่งั้นจะยุบลำน้ำทั้งสายให้เป็นระดับเดียว
MAX_SECTION_WIDTH = 16
# ระดับผิวน้ำใน run เดียวกันต่างกันได้ไม่เกินกี่บล็อกจึงจะถือว่าเป็น "หน้าตัด"
# artifact ที่ต้องแก้คือขั้น 1 บล็อกข้ามลำน้ำ ส่วน run ที่ต่างกันมากกว่านี้คือ
# ลำน้ำที่วิ่งเกือบขนานแกน — ยุบมันคือการขุดปล่อง (วัดได้ 72 บล็อกในรอบเดียว)
MAX_SECTION_SPREAD = 2

# ---- ความกว้างลำน้ำ ----
# OSM ไม่มีความกว้างจริงของลำธาร 89% ของเส้นถูกแท็กเป็น `stream` ซึ่งเป็น
# catch-all ครอบทั้งร่องน้ำบนสันเขาและลำธารที่รับน้ำจากสิบสาขา config จึงต้อง
# เดาค่าเดียวให้ทุกเส้น (RIVER_WIDTH_M) ผลคือทุกสายกว้างเท่ากันหมดตลอดความยาว
#
# ความยาวสายน้ำสะสมเหนือขึ้นไปเป็น proxy ของปริมาณน้ำ (Hack's law) และคำนวณได้
# จาก ordered way ที่มีอยู่แล้ว รัศมีจึงโตตาม log ของค่านั้น
# วัดกับข้อมูลจริง: กว้าง 1 บล็อก 89% -> 11.5%, 2 บล็อก 68.7%, 3 บล็อก 19.8%
# โดย footprint รวมไม่โต (x0.98)
STREAM_REFERENCE_BLOCKS = 22.0     # ความยาวสะสมของสายต้นน้ำ (p10 ของข้อมูลจริง)
STREAM_BASE_RADIUS = 0.55
STREAM_GROWTH = 0.18
MIN_STREAM_RADIUS = 0.70
MAX_STREAM_RADIUS = 2.5

# ผิวน้ำของลำน้ำ "ปกติ" ห้ามลดเกินหนึ่งบล็อกต่อก้าว — ที่ตกแรงกว่านี้ต้องเป็น
# น้ำตกที่ภูมิประเทศยืนยันเท่านั้น (ดู detect_terrain_cliffs)
#
# วัดจากข้อมูลจริงก่อนบังคับ: 99.7% ของ edge ในลำน้ำมี drop <= 1 อยู่แล้ว
# (แบน 88.2% + 1 บล็อก 11.5%) การบังคับจึงกระทบแค่ 0.26% ซึ่งเป็นส่วนที่ทำให้
# เกิด "เสาน้ำโดดกลางลำธาร" — 70.2% ของกลุ่มน้ำตกเป็นจุดเดี่ยวเพราะผิวน้ำใน
# แนวขวางลำน้ำไม่เท่ากัน มีแค่ cell เดียวที่ตกถึงเกณฑ์
STREAM_MAX_STEP = 1
# เกณฑ์เดียวที่ตัดสินว่า "ตรงนี้เป็นน้ำตก" — ภูมิประเทศเดิมต้องตกอย่างน้อยเท่านี้
# ในหนึ่งก้าว  ต้องใช้ค่าเดียวกันทั้งตอนปลดล็อก profile, ปลดล็อก envelope ของ
# raster และตอนตัดสินว่าจะเติมม่านน้ำ ถ้าสามที่ใช้คนละค่าจะเกิดจุดที่ผิวน้ำตกแรง
# ได้แต่ไม่มีม่านรองรับ = ช่องว่างกลางสายน้ำ
WATERFALL_TERRAIN_DROP = 3
# ผิวน้ำตกได้มากสุดเท่านี้เมื่อ *ไม่มี* หน้าผารองรับ — ต้องน้อยกว่าเกณฑ์สร้าง
# ม่านน้ำ (>=3) มิฉะนั้นจะมี drop ที่ไม่มีม่านมาปิด กลายเป็นช่องว่างกลางสายน้ำ
# เคยตั้งไว้เท่ากับ MAX_WATERFALL_DROP (4) แล้วเกิดช่องว่าง 4 บล็อกจริง
UNSUPPORTED_MAX_STEP = 2
# จำนวนรอบสูงสุดที่วน (envelope -> ยุบหน้าตัด) ให้ลู่เข้า ดู shape_waterway_patch
# 1 = พฤติกรรมเดิม (ทำอย่างละครั้ง) ซึ่งทิ้งขั้นที่ไม่มีม่านปิดไว้
#
# 4 ไม่พอ: วัดที่ flat_river แล้วรันการยุบหน้าตัดซ้ำบน *ผลสุดท้าย* ยังเปลี่ยนอีก
# 237 cell แปลว่าลูปจบก่อนนิ่ง แล้วเหลือขั้น 1-2 บล็อกวิ่งขนานลำน้ำไว้เงียบ ๆ
# ทั้งสองขั้นตอนกดลงอย่างเดียว การวนจึงหยุดเองแน่นอน เพดานนี้เป็นแค่กันลูปค้าง
SURFACE_FIXPOINT_PASSES = 16
# ผิวน้ำถูก envelope กดต่ำกว่า DEM เดิมของ cell นั้นได้มากสุดกี่บล็อก
#
# ลำน้ำจริงกัดร่องของตัวเองลึกได้ไม่กี่บล็อกที่สเกลนี้ (4 m/บล็อก) ค่าเดิมคือ
# *ไม่จำกัด* ซึ่งวัดได้ว่าให้ร่องลึก p50 38 / สูงสุด 122 บล็อก  6 เท่ากับ 24 m
# ซึ่งลึกกว่าร่องน้ำจริงในพื้นที่นี้อยู่แล้ว แต่ยังพอให้ envelope เกลี่ยขั้นได้
MAX_ENVELOPE_INCISION = 6
# ---- สวิตช์ม่านน้ำตก ----
# โมเดลม่านน้ำปัจจุบันคือ "ผิวน้ำตกแรงตรงไหน ให้เติมน้ำในคอลัมน์ล่างขึ้นไปจนถึง
# ระดับผิวน้ำของคอลัมน์บน" ซึ่งได้ภาพน้ำตกก็ต่อเมื่อคอลัมน์ล่างถูกหินล้อมสูงเท่า
# ปากน้ำตกอยู่แล้ว — เงื่อนไขที่โค้ดนี้ไม่เคยตรวจและไม่เคยสร้างให้
#
# วัดจากผังเต็ม 10000² ที่สร้างด้วยโมเดลนี้:
#   - 98.4% ของเสาม่าน (1,210/1,230) มีด้านเปิดอย่างน้อยหนึ่งด้าน
#     เปิดสองด้าน 63.1% เปิดสามด้าน 19.7% เหลือที่ถูกหินล้อมครบแค่ 20 เสา
#   - ม่าน 400 เสาที่ลึกสุด สูง median 4 บล็อก แต่ขั้นภูมิประเทศจริงข้างมัน
#     median 2 บล็อก (340/400 เสาสูงกว่าหน้าผาที่มีอยู่จริง)
#   - 24.1% ของวงแหวนแห้งรัศมี 3 บล็อกรอบม่านไม่ถูกแต่งเลย
# ผลในเกมคือ "เสาน้ำยืนโดดกลางลำธาร" ที่น้ำล้นออกทั้งซ้ายและขวา
#
# ทางแก้ที่ *ไม่* ได้ผล และเหตุผลที่ต้องบันทึกไว้: เคยลองปิดม่านทั้งหมดโดยเลิก
# ปลดล็อก envelope ที่หน้าผา ผลคือ `limit_masked_steps` ต้องไถส่วนเกินเองทั้งหมด
# ระยะที่มันแพร่จึงโตตามความสูงหน้าผา วัดแล้วบนผัง 128² ที่มีหน้าผา 60 บล็อก:
# tile ที่ core อยู่แถว 0..31 มีหน้าต่างถึงแถว 63 จึงมองไม่เห็นหน้าผาที่แถว 64 เลย
# global เพี้ยนจาก patch (ขั้นสูงสุด 17 vs 2) ที่ halo ทั้ง 8 และ 32
# — ไม่มีค่า halo ใดปลอดภัย เพราะขอบเขตขึ้นกับข้อมูล ไม่ใช่ค่าคงที่
#
# จึงเก็บการปลดล็อกไว้ (ทำให้ผลไม่ขึ้นกับการแบ่ง tile) แล้วแก้ที่ต้นเหตุจริงสองข้อ
# คือ dilation ของ `supported` และการไม่ปิดผนังข้างม่าน — ดู terrain_cliff_support
# กับ seal_waterfall_banks
ENABLE_WATERFALL_CURTAINS = True

# ที่รอยต่อลำธาร->ทะเลสาบ การตรวจหน้าผาถูกยกเว้นทั้งหมด แปลว่าน้ำตกลงทะเลสาบ
# ไม่ต้องมีภูมิประเทศรองรับเลย
#
# วัดแล้วบน patch (2242, 3057): ปิดข้อยกเว้นนี้ทำให้ม่านน้ำเหลือ **ศูนย์** จาก 117
# เสา — แปลว่าเสาม่านทุกต้นในบริเวณนั้นไม่มีหน้าผาอยู่ข้างหลังแม้แต่ต้นเดียว
# มันไม่ใช่น้ำตก แต่เป็นผลของการที่ผิวลำธารค้างสูงกว่าทะเลสาบ 12 บล็อกเพราะ
# การ pin ปากน้ำ (`mouth`) ถูกจำกัดไว้ที่ |diff| <= max_surface_step
#
# **แต่ปิดเฉย ๆ ไม่ใช่คำตอบ** เพราะจะเหลือช่องว่าง 12 บล็อกระหว่างลำธารกับ
# ทะเลสาบแทน คำตอบที่ถูกคือทำให้ลำธารไหลลงถึงระดับทะเลสาบจริง ๆ (ยกเลิกเพดาน
# ของ mouth แล้วให้ envelope ไถลงมา) ซึ่งเป็นงานคนละก้อน — ดู Phase C
#
# เปิดไว้ก่อนเพื่อให้โค้ดตรงกับ product ที่สร้างไว้แล้ว ห้ามปิดโดยไม่แก้ mouth
LAKE_MOUTH_CURTAIN_EXEMPTION = True

# ---- ทางที่ลองแล้วไม่ได้ผล: ท่วมริมฝั่งแทนการก่อขอบ ----
# แนวคิดคือพื้นที่ที่ OSM บอกว่าเป็นบกแต่ DEM ต่ำกว่าผิวน้ำข้าง ๆ ควรถูกน้ำท่วม
# แทนที่จะก่อขอบดินกันไว้ (DEM น่าเชื่อกว่าขอบ polygon ที่วาดด้วยมือในระยะ 1-2
# บล็อก)  วัดจริงที่ patch (5970, 5902) แล้ว **คันดินเพิ่มขึ้น** ไม่ใช่ลดลง:
#   วง 0 -> คันดิน >=1 429 cells | วง 2 -> 465 | วง 3 -> 498 (น้ำอ้วนขึ้น 11.5%)
# เพราะทุกวงที่ท่วมสร้างขอบน้ำ/บกใหม่ที่ไกลออกไป และพื้นตรงนั้นก็ต่ำกว่าระดับน้ำ
# เหมือนกัน (มันถึงได้ราบและต่ำ) การท่วมจึงย้ายที่คันดิน ไม่ได้ลบมัน
#
# อย่ารื้อกลับมาโดยไม่มีวิธีเลือก "ระดับน้ำที่พอดีกับพื้นตรงนั้น" มาก่อน

# ความยาวแอ่งขั้นต่ำเป็น "บล็อกแนวราบ" — วัดจากผังจริงว่าความชันรวมของลำธาร
# ทั้งแผนที่ (163,184 บล็อกที่ต้องลด กระจายบน 1.3M คู่ cell) ให้ขั้น 1 บล็อก
# ทุก ๆ 8.1 cell ถ้าปล่อยให้ลดตาม DEM ทันทีจะได้ median แค่ 5
MIN_POOL_BLOCKS = 8
# ระยะที่ระดับผิวน้ำของร่อง centerline แผ่ออกไปคลุมผืนน้ำที่ OSM แมปไว้
# ต้องน้อยกว่า GLOBAL_HALO - MAX_STREAM_RADIUS ไม่งั้น EDT ของแต่ละ tile เห็น
# centerline ไม่ครบแล้ว --global เพี้ยนจาก --patch
LATERAL_STAGE_REACH = 5.0
# ระยะระหว่าง sample ของ profile — ต้องตรงกับ default ของ densify_polyline
# ค่านี้เป็นตัวแปลง "บล็อกต่อบล็อก" เป็น "บล็อกต่อ sample" ถ้าไม่ตรงกัน การ
# จำกัดความชันจะผิดหน่วยแบบเงียบ ๆ
DENSIFY_SPACING = 0.5


def mouth_pin_max_drop():
    """ดึงผิวลำธารลงหาระดับทะเลสาบได้มากสุดกี่บล็อกต่อหนึ่งปากน้ำ

    ต้องผูกกับ ``GLOBAL_HALO`` ไม่ใช่ตัวเลขลอย ๆ เพราะ `limit_masked_steps` ต้อง
    ไถการลดนี้ขึ้นไปทางต้นน้ำที่ ``UNSUPPORTED_MAX_STEP`` ต่อก้าว ระยะแพร่จึงเป็น
    drop / step ถ้าเกิน halo ผลของ --global จะเพี้ยนจาก --patch โดยไม่มีอะไรฟ้อง
    นอกจาก seam_step_report (เคยวัดได้แล้วว่าเพี้ยนจริง: ขั้น 17 เทียบกับ 2)

    เหลือระยะกันชน 2 cell ไว้ให้กฎอื่นที่แพร่สั้น ๆ ในรอบเดียวกัน

    เพดานบนคือ ``max_channel_adjust`` (12) เพราะช่องว่างที่ปากน้ำจริงบนผังนี้
    สูงสุด 12 บล็อกพอดี การขยาย GLOBAL_HALO เพื่อเหตุผลอื่นจึงต้องไม่ไปปลดล็อก
    ให้ดึงลงลึกกว่าที่เคยวัดไว้โดยไม่ตั้งใจ
    """
    return min(
        12,
        max(
            int(UNSUPPORTED_MAX_STEP),
            int(UNSUPPORTED_MAX_STEP) * max(1, int(GLOBAL_HALO) - 2),
        ),
    )


def cliff_drop_per_sample():
    """เกณฑ์ปลดล็อก profile ที่หน้าผา — None เมื่อปิดม่านน้ำ

    ต้องเป็นที่เดียวที่ตอบคำถามนี้ ทั้ง `build_line_profiles` และทางที่
    `shape_waterway_patch` สร้าง profile เองต้องเรียกตัวนี้ ไม่งั้นสองเส้นทาง
    จะให้ profile คนละแบบ ซึ่งเป็นบั๊กแบบเดียวกับที่ `stream_radius` เคยเป็น
    """
    return (
        WATERFALL_TERRAIN_DROP * DENSIFY_SPACING
        if ENABLE_WATERFALL_CURTAINS else None
    )


def terrain_cliff_support(terrain, mask, side="lip"):
    """cell ที่หน้าผาจริงยืนยัน — ``side`` ตามความหมายใน detect_terrain_cliffs

    ``"lip"`` ใช้ปลดล็อก envelope ส่วน ``"foot"`` ใช้เป็นเงื่อนไขสร้างม่านน้ำ
    ทั้งคู่ต้องมาจากฟังก์ชันนี้ตัวเดียว เพื่อให้ "ที่ไหนผิวน้ำตกแรงได้" กับ
    "ที่ไหนมีม่านมารองรับ" เป็นขอบเดียวกันเสมอ ไม่งั้นเกิดช่องว่างกลางสายน้ำ

    **ห้ามใส่ dilation กลับเข้ามา** เดิมผลของ `detect_terrain_cliffs` ถูก
    `binary_dilation` ด้วย structure 3x3 ก่อนใช้ ผลคือหน้าผาจริงเพียง 3 บล็อก
    ปลดล็อกให้ผิวน้ำตกได้ *ไม่จำกัด* กับทุก cell ในรัศมี 1 บล็อกรอบตัว

    วัดจากผังเต็มที่สร้างด้วย dilation: ม่าน 400 เสาที่ลึกสุดสูง median 4 บล็อก
    แต่ขั้นภูมิประเทศจริงข้างมัน median 2 บล็อก และ 340/400 เสาสูงกว่าหน้าผาที่
    มีอยู่จริง — นี่คือ "น้ำตกสูงขึ้นมาผิดปกติ" ที่เห็นในเกม สูงสุดวัดได้ 12
    บล็อกทั้งที่ขั้น DEM ตามลำน้ำทั้งแผนที่สูงสุดแค่ 9 (99.99% ไม่เกิน 4)

    ไม่มี dilation แล้ว ม่านจะสูงได้ไม่เกินหน้าผาที่ภูมิประเทศมีจริงตรงขอบนั้น
    """
    if not ENABLE_WATERFALL_CURTAINS:
        return np.zeros(np.asarray(mask).shape, dtype=bool)
    return detect_terrain_cliffs(
        terrain, mask, min_drop=WATERFALL_TERRAIN_DROP, side=side,
    )


def build_step_weirs(terrain, water, body, way, surface, depth, kind,
                     waterfall_top=None,
                     min_neighbours=WEIR_MIN_WET_NEIGHBOURS,
                     open_share=WEIR_OPEN_SHARE, x0=0, z0=0):
    """วางสันหินขวางลำน้ำตรงที่ผิวน้ำลดระดับ เว้นช่องให้น้ำไหลผ่าน

    ปัญหา: บนลำน้ำ *กว้าง* ที่ผิวน้ำลดทีละบล็อก แต่ละขั้นเป็นแผ่นสี่เหลี่ยมเต็ม
    ความกว้าง ตาอ่านว่าเป็นขั้นบันไดคอนกรีต ไม่ใช่แม่น้ำ  และเมื่อวานิลลา tick
    น้ำ ฝั่งสูงจะแผ่ลงฝั่งต่ำเต็มหน้ากว้าง กลายเป็น source ใหม่จนระดับถูกยกถาวร

    แม่น้ำจริงลดระดับที่ *แก่ง* คือสันหินขวางลำน้ำที่มีช่องให้น้ำพุ่งผ่าน
    ที่นี่จึงเปลี่ยน cell ริมบนของขั้น (lip) ให้เป็นหินระดับเดียวกับผิวน้ำบน
    เหลือช่องเปิดตาม noise พิกัดโลก น้ำจึงถูกขังไว้ด้วยสันหินและไหลผ่านเฉพาะช่อง
    — ได้ทั้งภาพแก่งและหยุดการแผ่เต็มหน้ากว้าง

    ใช้เฉพาะลำน้ำกว้าง (``min_neighbours``) ลำธารบนภูเขากว้าง 1-2 บล็อกไม่ถูกแตะ
    เพราะขั้นของมันอ่านเป็นแก่งน้ำอยู่แล้ว (ผู้เล่นยืนยันว่าสวย)

    noise ยึดพิกัดโลกจึงให้ผลเท่ากันทุก tile ไม่ต้องมองข้ามขอบ
    """
    terrain = np.asarray(terrain, dtype=np.int32).copy()
    water = np.asarray(water, dtype=bool).copy()
    body = np.asarray(body, dtype=bool).copy()
    way = np.asarray(way, dtype=bool).copy()
    surface = np.asarray(surface, dtype=np.int16).copy()
    depth = np.asarray(depth, dtype=np.uint8).copy()
    kind = np.asarray(kind, dtype=np.uint8).copy()
    waterfall_top = (
        None if waterfall_top is None
        else np.asarray(waterfall_top, dtype=np.int16).copy()
    )

    wet_neighbours = np.zeros(water.shape, dtype=np.int32)
    lip = np.zeros(water.shape, dtype=bool)
    for dst, src in (
        (np.s_[1:, :], np.s_[:-1, :]),
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[:, 1:], np.s_[:, :-1]),
        (np.s_[:, :-1], np.s_[:, 1:]),
    ):
        wet_neighbours[dst] += water[src]
        # cell ฝั่ง "บน" ของขั้น คือฝั่งที่ผิวน้ำสูงกว่าเพื่อนบ้านที่เป็นน้ำ
        lip[src] |= water[src] & water[dst] & (surface[src] > surface[dst])

    candidate = lip & way & (wet_neighbours >= int(min_neighbours))
    if not ENABLE_STEP_WEIRS:
        candidate = np.zeros(water.shape, dtype=bool)
    if not candidate.any():
        return (terrain, water, body, way, surface, depth, kind,
                waterfall_top, candidate)

    height, width = water.shape
    gap = S.smooth_noise(int(x0), int(z0), (width, height), 6.0, 4517, 2).T
    # กระจายช่องเปิดเป็นหย่อม ๆ ไม่ใช่เม็ดสลับ — ดูเป็นก้อนหินจริงมากกว่า
    #
    # ต้องเป็นเกณฑ์ *คงที่* ห้ามใช้ quantile ของหน้าต่าง เพราะค่านั้นขึ้นกับว่า
    # หน้าต่างครอบอะไรบ้าง tile กับ patch จึงได้สันหินคนละที่ (เทสต์ parity จับได้)
    opening = gap > float(open_share)
    sill = candidate & ~opening
    if not sill.any():
        return (terrain, water, body, way, surface, depth, kind,
                waterfall_top, sill)

    # สันหินอยู่ระดับเดียวกับผิวน้ำฝั่งบน น้ำจึงถูกกั้นแทนที่จะแผ่ข้ามเต็มหน้า
    terrain[sill] = surface[sill].astype(np.int32)
    water[sill] = False
    way[sill] = False
    body[sill] = False
    depth[sill] = 0
    kind[sill] = 0
    # ต้องล้าง surface/waterfall_top ด้วย ไม่งั้น product จะมี "ระดับผิวน้ำ" อยู่บน
    # cell ที่ไม่ใช่น้ำแล้ว — paint_surface ตรวจความตรงกันของสองอย่างนี้และจะหยุด
    surface[sill] = UNRESOLVED
    if waterfall_top is not None:
        waterfall_top[sill] = UNRESOLVED
    return (terrain, water, body, way, surface, depth, kind,
            waterfall_top, sill)


def seal_waterfall_banks(terrain, water, surface, waterfall_top):
    """ยกพื้นแห้งรอบม่านน้ำให้ถึงยอดม่าน ไม่ใช่แค่ถึงผิวน้ำ

    การยกเพื่อนบ้านแห้งรอบสุดท้ายเดิมใช้ `surface` อย่างเดียว แต่ม่านน้ำตกอยู่
    *เหนือ* ผิวน้ำขึ้นไปถึง `waterfall_top` ตลิ่งจึงกันได้แค่ระดับผิวน้ำ ส่วนม่าน
    ไม่มีอะไรกันเลย

    วัดจากผังเต็ม: 98.4% ของเสาม่านมีด้านเปิดอย่างน้อยหนึ่งด้าน (เปิดสองด้าน
    63.1%) และมีน้ำที่ไหลออกได้ 8,336 บล็อก ผลในเกมคือเสาน้ำยืนโดดที่น้ำล้นออก
    ทั้งซ้ายและขวา ตรงกับที่ผู้เล่นรายงาน

    ยกถึงยอดม่านแล้วม่านจะอยู่ในร่องหินเหมือนน้ำตกจริง เหลือเปิดเฉพาะด้านที่เป็น
    น้ำด้วยกัน ซึ่งคือทางที่น้ำตกลงจริง
    """
    terrain = np.asarray(terrain, dtype=np.int32).copy()
    water = np.asarray(water, dtype=bool)
    surface = np.asarray(surface, dtype=np.int16)
    waterfall_top = np.asarray(waterfall_top, dtype=np.int16)
    wet_top = np.where(
        water,
        np.maximum(surface.astype(np.int32), waterfall_top.astype(np.int32)),
        UNRESOLVED,
    )
    for dst, src in (
        (np.s_[1:, :], np.s_[:-1, :]),
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[:, 1:], np.s_[:, :-1]),
        (np.s_[:, :-1], np.s_[:, 1:]),
    ):
        dry = ~water[dst] & water[src]
        view = terrain[dst]
        view[dry] = np.maximum(view[dry], wet_top[src][dry])
    return terrain


def accumulate_upstream_length(sources):
    """ความยาวสายน้ำสะสมที่ไหลผ่านปลายของแต่ละเส้น (หน่วยบล็อก)

    ต่อ ordered way เข้าด้วยกันโดยจับคู่ "ปลายของ A" กับ "ต้นของ B" ที่ปัดเป็น
    บล็อกเดียวกัน แล้วไล่สะสมแบบ topological (Kahn) กับข้อมูลจริงเชื่อมได้
    69.6% ของเส้นและไม่มี cycle เลย

    ถ้ามี cycle เส้นในวงจะไม่ถูกปล่อยจากคิวและคงค่าเป็นความยาวของตัวเอง ซึ่ง
    ปลอดภัย (ได้ลำน้ำแคบ) ดีกว่าวนไม่รู้จบ
    """
    offsets = np.asarray(sources["offsets"], dtype=np.int64)
    points_x = np.asarray(sources["points_x"], dtype=np.float64)
    points_z = np.asarray(sources["points_z"], dtype=np.float64)
    count = len(offsets) - 1
    if count <= 0:
        return np.zeros(0, dtype=np.float64)

    spans = [slice(int(offsets[i]), int(offsets[i + 1])) for i in range(count)]
    accumulated = np.array([
        float(np.hypot(
            np.diff(points_x[span]), np.diff(points_z[span])
        ).sum())
        for span in spans
    ])

    starting_at = {}
    for i, span in enumerate(spans):
        key = (round(points_x[span.start]), round(points_z[span.start]))
        starting_at.setdefault(key, []).append(i)

    successors = [[] for _ in range(count)]
    indegree = np.zeros(count, dtype=np.int64)
    for i, span in enumerate(spans):
        key = (round(points_x[span.stop - 1]), round(points_z[span.stop - 1]))
        for j in starting_at.get(key, ()):
            if j != i:
                successors[i].append(j)
                indegree[j] += 1

    queue = [i for i in range(count) if indegree[i] == 0]
    while queue:
        i = queue.pop()
        for j in successors[i]:
            accumulated[j] += accumulated[i]
            indegree[j] -= 1
            if indegree[j] == 0:
                queue.append(j)
    return accumulated


def stream_radius(width_m, upstream_blocks):
    """รัศมีลำน้ำเป็นบล็อก — ต้องเป็นที่เดียวที่ตัดสินความกว้าง

    ทั้ง `build_line_profiles` (ทางของ --global) และทางที่ shape_waterway_patch
    สร้าง profile เองต้องเรียกตัวนี้ ไม่งั้นสองเส้นทางจะให้ลำน้ำคนละความกว้าง
    ซึ่งเป็นบั๊กแบบเดียวกับที่ max_drop_per_sample เคยเป็น
    """
    from_kind = max(
        STREAM_BASE_RADIUS, float(width_m) / (2.0 * C.METERS_PER_BLOCK)
    )
    from_flow = STREAM_BASE_RADIUS + STREAM_GROWTH * float(np.log(
        max(1.0, float(upstream_blocks) / STREAM_REFERENCE_BLOCKS)
    ))
    return float(np.clip(
        max(from_kind, from_flow, MIN_STREAM_RADIUS),
        MIN_STREAM_RADIUS, MAX_STREAM_RADIUS,
    ))


def bank_taper_weight(distance, seal_blocks=BANK_SEAL_BLOCKS,
                      blend_blocks=BANK_BLEND_BLOCKS):
    """น้ำหนักการขึ้นรูปตลิ่ง — 1.0 ที่ติดน้ำ ลดเชิงเส้นจนเป็น 0 ที่ blend_blocks

    เป็นตัวเดียวที่กำหนดว่าการขึ้นรูปตลิ่ง "จางหาย" อย่างไร ทั้งการยกและการกด
    ต้องใช้ตัวนี้ร่วมกัน ไม่งั้นสองด้านจะจางคนละอัตราแล้วเห็นเป็นขอบอีก
    """
    distance = np.asarray(distance, dtype=np.float32)
    seal = float(seal_blocks)
    blend = np.maximum(seal + 1e-6, np.asarray(blend_blocks, dtype=np.float32))
    return np.clip((blend - distance) / (blend - seal), 0.0, 1.0)


def taper_bank_lift(terrain, near_stage, distance, seal_blocks=BANK_SEAL_BLOCKS,
                    blend_blocks=BANK_BLEND_BLOCKS, where=None):
    """ยกตลิ่งเท่าที่กันน้ำรั่ว แล้วเฟดกลับสู่ภูมิประเทศเดิม

    สูตรเดิมคือ ``minimum = near_stage + (d - 1)`` ซึ่ง **สูงขึ้นตามระยะ** แล้ว
    ตัดจบทันทีที่ ``bank_width`` ผลคือถ้าพื้นเดิมลาดลงออกจากน้ำ (ทะเลสาบบนไหล่
    เขา ซึ่งมีเยอะมากในผังนี้) ทั้งแถบกว้าง 5 บล็อกจะถูกยกเป็นสันเหนือผิวน้ำ
    แล้วเกิดหน้าผาสูงหลายสิบบล็อกที่ขอบแถบ — นี่คือ "บล็อกรอบน้ำยกสูงจนไม่
    blend" ที่เห็นในเกม

    ที่จำเป็นจริง ๆ มีอย่างเดียว: บล็อกที่ *ติดน้ำ* ต้องไม่ต่ำกว่าผิวน้ำ ไม่งั้น
    น้ำไหลออก ที่ไกลกว่านั้นปล่อยให้ภูมิประเทศเป็นตัวของมันเอง จึงยกเต็มที่
    ``seal_blocks`` แล้วลดน้ำหนักเชิงเส้นจนเป็นศูนย์ที่ ``blend_blocks``

    พื้นที่สูงกว่าผิวน้ำอยู่แล้วจะไม่ถูกแตะเลย หน้าผาธรรมชาติจึงยังอยู่ครบ
    """
    terrain = np.asarray(terrain, dtype=np.int32)
    near_stage = np.asarray(near_stage, dtype=np.int32)
    distance = np.asarray(distance, dtype=np.float32)
    if terrain.shape != near_stage.shape or terrain.shape != distance.shape:
        raise ValueError("terrain, near_stage, and distance must match")

    weight = bank_taper_weight(distance, seal_blocks, blend_blocks)
    weight = np.where(distance <= float(seal_blocks), 1.0, weight)
    deficit = np.maximum(0, near_stage - terrain).astype(np.float32)
    lift = np.rint(deficit * weight).astype(np.int32)
    if where is not None:
        lift = np.where(np.asarray(where, dtype=bool), lift, 0)
    return terrain + lift


def hillside_rise_per_block(terrain, minimum=1.0, maximum=6.0, smooth=3):
    """ความชันของภูมิประเทศเดิม เป็นบล็อกต่อบล็อก — เพดานการลาดตลิ่ง

    `taper_bank_cut` เดิมยอมให้ตลิ่งสูงขึ้นได้ 1 บล็อกต่อระยะ 1 บล็อกตายตัว
    (ลาด 45 องศา) วัดจากผังจริงแล้วไหล่เขารอบลำธารชัน p50 = 1.1 และ p90 = 3.5
    บล็อก/บล็อก เกินครึ่งของแถบตลิ่งบนภูเขาจึงชันกว่าที่สูตรยอม แล้วถูกไถให้
    เป็นชั้นราบตามแนวลำธาร — ผู้เล่นอ่านว่าเป็น "ถนน" ตัดผ่านภูเขา
    (วัดได้: ไถลึกถึง -5 ยกถึง +8 สองฝั่งไม่เท่ากัน 88% ของแถว)

    ให้เพดานไต่ตามความชันจริงของไหล่เขาแทน ตลิ่งจึงกลืนกับภูมิประเทศรอบข้าง
    ส่วนที่ราบ (ชัน ~0) ยังใช้ค่าต่ำสุด 1.0 เหมือนเดิม พฤติกรรมตรงนั้นไม่เปลี่ยน
    """
    terrain = np.asarray(terrain, dtype=np.float32)
    gz, gx = np.gradient(terrain)
    slope = np.hypot(gz, gx)
    if smooth and smooth > 1:
        slope = ndimage.uniform_filter(slope, size=int(smooth), mode="nearest")
    return np.clip(slope, float(minimum), float(maximum))


def taper_bank_cut(terrain, near_stage, distance, margin=0,
                   seal_blocks=BANK_SEAL_BLOCKS,
                   blend_blocks=BANK_BLEND_BLOCKS, where=None,
                   rise_per_block=None):
    """ลาดตลิ่งลงหาน้ำ โดยเฟดกลับเช่นเดียวกับการยก

    `regularize_stage` กดผิวน้ำให้ต่ำกว่า DEM (isotonic + จำกัด drop) ลำธารจึง
    จมลงไปในพื้นราว 1 บล็อก ส่วนพื้นข้างยังอยู่ระดับเดิม ถ้าไม่ลาดตลิ่งลงมาจะ
    ได้ **ร่องผนังตั้งฉาก** ซึ่งตาอ่านว่าเป็นขอบคมรอบลำธาร — วัดที่ (7296,4480)
    ได้ 38.2% ของบล็อกแห้งที่ติดน้ำสูงกว่าผิวน้ำ

    เพดานคือ ``near_stage + (ceil(distance) - 1) + margin`` แถวที่ติดน้ำจึงถูกกด
    ลงมา *เสมอผิวน้ำพอดี* (ไม่ใช่ต่ำกว่า จึงยังกันน้ำรั่วได้) แล้วไต่ขึ้นทีละ
    บล็อกต่อระยะหนึ่งบล็อก ได้ตลิ่งลาดแทนผนังตั้ง

    margin เดิมตั้งไว้ 1 ทำให้เพดานสูงกว่าพื้นจริงหนึ่งระดับเสมอ ฟังก์ชันนี้จึง
    ไม่เคยกดอะไรเลย — เป็น no-op ที่ดูเหมือนทำงาน
    """
    terrain = np.asarray(terrain, dtype=np.int32)
    near_stage = np.asarray(near_stage, dtype=np.int32)
    distance = np.asarray(distance, dtype=np.float32)
    rings = np.maximum(0, np.ceil(distance).astype(np.int32) - 1)
    rise = (
        1.0 if rise_per_block is None
        else np.asarray(rise_per_block, dtype=np.float32)
    )
    ceiling = near_stage + np.rint(rings * rise).astype(np.int32) + int(margin)
    excess = np.maximum(0, terrain - ceiling).astype(np.float32)
    # เพดานการกด **บวก** ไม่ใช่ **คูณ**
    #
    # เดิมใช้ `excess * weight` โดย weight ไล่จาก 1 ลง 0 ตามระยะ ผลคือขนาดการกด
    # แปรตาม excess ซึ่งบนไหล่เขาสูงถึง 40 บล็อก ระหว่างวงที่ห่างกันหนึ่งบล็อก
    # weight ต่างกัน ~0.14 การกดจึงต่างกัน ~5 บล็อก = ตลิ่งเป็นบันไดหิน
    # วัดที่ hill_junction: ฟังก์ชันนี้ทำให้ตลิ่งที่ปีนเกิน 1 บล็อกพุ่งจาก 7%
    # เป็น 31% และขั้นสูงสุด 4 -> 24 ทั้งที่หน้าที่มันคือ "ลาดตลิ่งให้เดินลงน้ำได้"
    #
    # เพดานแบบบวกทำให้การกดของวงที่ติดกันต่างกันไม่เกิน `rise` ต่อบล็อกเสมอ
    # ไม่ว่า excess จะใหญ่แค่ไหน และยังเฟดเป็นศูนย์พอดีที่ blend_blocks เหมือนเดิม
    allowance = np.maximum(
        0.0, (np.asarray(blend_blocks, dtype=np.float32) - distance)
    ) * rise
    cut = np.rint(np.minimum(excess, allowance)).astype(np.int32)
    if where is not None:
        cut = np.where(np.asarray(where, dtype=bool), cut, 0)
    return terrain - cut


def densify_polyline(x, z, spacing=DENSIFY_SPACING):
    """Sample an ordered polyline at near-uniform world-block spacing."""
    x = np.asarray(x, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    if x.shape != z.shape or x.ndim != 1 or len(x) < 2:
        raise ValueError("x and z must be matching 1D arrays with >=2 points")
    segment = np.hypot(np.diff(x), np.diff(z))
    cumulative = np.concatenate(([0.0], np.cumsum(segment)))
    keep = np.concatenate(([True], np.diff(cumulative) > 1e-9))
    x, z, cumulative = x[keep], z[keep], cumulative[keep]
    if len(x) < 2 or cumulative[-1] <= 0:
        return x.copy(), z.copy()
    samples = np.arange(0.0, cumulative[-1], float(spacing))
    if not len(samples) or samples[-1] < cumulative[-1]:
        samples = np.append(samples, cumulative[-1])
    return (
        np.interp(samples, cumulative, x),
        np.interp(samples, cumulative, z),
    )


def isotonic_nonincreasing(values):
    """Least-squares non-increasing fit using pool-adjacent violators."""
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("values must be 1D")
    sums = []
    weights = []
    starts = []
    ends = []
    for i, value in enumerate(values):
        sums.append(float(value))
        weights.append(1.0)
        starts.append(i)
        ends.append(i + 1)
        while (
            len(sums) >= 2
            and sums[-2] / weights[-2] < sums[-1] / weights[-1]
        ):
            sums[-2] += sums[-1]
            weights[-2] += weights[-1]
            ends[-2] = ends[-1]
            sums.pop()
            weights.pop()
            starts.pop()
            ends.pop()
    result = np.empty(values.shape, dtype=np.float64)
    for total, weight, start, end in zip(sums, weights, starts, ends):
        result[start:end] = total / weight
    return result


def regularize_stage(raw_y, median_window=9, max_drop_per_sample=1.0,
                     gentle_drop_per_sample=None, cliff_drop_per_sample=None,
                     pool_samples=None, pool_confine=None, pool_max_above=2,
                     pool_width=None, incise_ceiling=None):
    """Build a monotone stream stage without DEM spikes or upward pools.

    ``gentle_drop_per_sample`` จำกัดการลดของผิวน้ำในช่วงลำน้ำ *ปกติ* ให้เรียบ
    ส่วน sample ที่ภูมิประเทศเดิมตกเกิน ``cliff_drop_per_sample`` จะได้รับอนุญาต
    ให้ตกเต็มที่ — นี่คือเส้นแบ่ง "น้ำตก" กับ "ลำธารชัน" ที่ WATER_REDESIGN
    เรียกร้อง

    ``pool_confine`` คือความสูงตลิ่งจริงต่อ sample (จาก bank_confinement) เมื่อ
    ให้มา การยืดแอ่งจะขังน้ำเหนือพื้นได้เฉพาะเท่าที่ตลิ่งนั้นขังไว้จริง โดย
    filter ด้วยหน้าต่าง median เดียวกับ profile เพื่อไม่ให้ตลิ่งแหว่งหนึ่ง
    sample ตัดแอ่งทั้งอัน

    ทำที่ profile 1D ไม่ใช่บน raster เพราะ profile ถูกสร้างครั้งเดียวจากทั้ง
    เครือข่าย การบังคับที่นี่จึงให้ผลเท่ากันไม่ว่าจะแบ่ง tile อย่างไร ส่วนการ
    ทำแบบเดียวกันบน raster (`limit_masked_steps` step เล็ก) ทำให้ระยะแพร่ยาว
    ขึ้นตามส่วน จน halo ของ tile ไม่พอและ --global เพี้ยนจาก --patch ทันที
    """
    raw_y = np.asarray(raw_y, dtype=np.float64)
    if raw_y.ndim != 1:
        raise ValueError("raw_y must be 1D")
    if not len(raw_y):
        return np.zeros(0, dtype=np.int16)
    window = max(1, int(median_window) | 1)
    smooth = ndimage.median_filter(raw_y, size=window, mode="nearest")
    stage = isotonic_nonincreasing(smooth)
    max_drop = max(0.01, float(max_drop_per_sample))

    allowed = np.full(max(0, len(stage) - 1), max_drop, dtype=np.float64)
    if gentle_drop_per_sample is not None and allowed.size:
        gentle = max(0.01, float(gentle_drop_per_sample))
        allowed[:] = gentle
        if cliff_drop_per_sample is not None:
            # ใช้ profile ที่ median filter แล้วเป็นตัวตัดสิน ไม่ใช่ raw ดิบ
            # ไม่งั้น spike เดี่ยว ๆ ของ DEM จะถูกนับเป็นหน้าผา
            steep = -np.diff(smooth) >= float(cliff_drop_per_sample)
            allowed[steep] = np.inf

    # Resolve a cliff by lowering the approach toward its downstream control.
    # Raising the reach below a cliff creates suspended water and forces the
    # bank shaper to build enormous artificial walls.
    for i in range(len(stage) - 1, 0, -1):
        stage[i - 1] = min(stage[i - 1], stage[i] + allowed[i - 1])
    stage = np.minimum.accumulate(np.rint(stage).astype(np.int32))

    # ขุดร่องแทนการก่อคันดิน
    #
    # บนที่ราบน้ำท่วมถึง ตลิ่งอยู่ระดับเดียวกับผิวน้ำพอดี (วัดได้ p50 +0.0) ทุก
    # หลุมเล็ก ๆ บนที่ราบจึงต้องถูกก่อขอบกันน้ำ ได้ริมดินยาวฝั่งเดียวตามแนวแม่น้ำ
    # ซึ่งผู้เล่นอ่านว่า "ตลิ่งเป็นกำแพงและสองฝั่งไม่เท่ากัน"
    #
    # วางผิวน้ำให้ต่ำกว่าตลิ่งจริงแทน แล้วตลิ่งจะสูงกว่าน้ำเองทั้งสองฝั่งโดยไม่
    # ต้องก่ออะไรเลย — เป็นวิธีเดียวกับที่ community ใช้สร้างแม่น้ำ (ผิวน้ำราบ
    # ยาว ๆ ในร่องที่ขุดไว้) ผู้เรียกต้องส่งเพดานเฉพาะช่วงที่ราบเท่านั้น
    # ลำธารบนไหล่เขาไม่ต้องขุดเพราะมันอยู่ในร่องเขาอยู่แล้ว
    if incise_ceiling is not None:
        ceiling = np.asarray(incise_ceiling, dtype=np.float64)
        if ceiling.shape != raw_y.shape:
            raise ValueError("incise_ceiling must match raw_y")
        lowered = np.minimum(stage.astype(np.float64), ceiling)
        # การกดลงอาจทำให้ช่วงเหนือน้ำชันเกินกฎ ต้องไล่ปรับขึ้นไปทางต้นน้ำอีกครั้ง
        for i in range(len(lowered) - 1, 0, -1):
            lowered[i - 1] = min(lowered[i - 1], lowered[i] + allowed[i - 1])
        stage = np.minimum.accumulate(np.rint(lowered).astype(np.int32))
    if pool_samples:
        if pool_confine is None:
            max_above = pool_max_above
        else:
            pool_confine = np.asarray(pool_confine, dtype=np.float64)
            if pool_confine.shape != raw_y.shape:
                raise ValueError("pool_confine must match raw_y")
            confine = ndimage.median_filter(
                pool_confine, size=window, mode="nearest"
            )
            max_above = np.clip(confine - smooth, 0.0, float(pool_max_above))
        step_allowed = None
        if pool_width is not None:
            pool_width = np.asarray(pool_width, dtype=np.int32)
            if pool_width.shape != raw_y.shape:
                raise ValueError("pool_width must match raw_y")
            step_allowed = local_narrow_points(pool_width, pool_samples)
        stage = quantize_stage_pools(
            stage, smooth, pool_samples, max_above=max_above,
            step_allowed=step_allowed,
        )
    return stage.astype(np.int16)


def channel_width(waterbody, sample_x, sample_z, max_half=8):
    """ความกว้างของผืนน้ำที่ตั้งฉากกับเส้นลำน้ำ ต่อ sample (หน่วยบล็อก)

    เดินออกจาก centerline ทีละบล็อกทั้งสองข้างจนหลุดผืนน้ำ นับเฉพาะช่วงที่
    ติดกันจริง (ใช้ cumulative product) ไม่งั้นผืนน้ำอีกก้อนที่อยู่เลยตลิ่งไป
    จะถูกนับรวมเป็นความกว้างของลำน้ำนี้
    """
    waterbody = np.asarray(waterbody, dtype=bool)
    sample_x = np.asarray(sample_x, dtype=np.float64)
    sample_z = np.asarray(sample_z, dtype=np.float64)
    height, width = waterbody.shape
    dx = np.gradient(sample_x)
    dz = np.gradient(sample_z)
    length = np.hypot(dx, dz)
    length[length == 0] = 1.0
    normal_x, normal_z = -dz / length, dx / length

    total = np.ones(sample_x.shape, dtype=np.int32)
    for sign in (1.0, -1.0):
        run = np.ones(sample_x.shape, dtype=bool)
        for step in range(1, int(max_half) + 1):
            bx = np.clip(
                np.rint(sample_x + sign * normal_x * step).astype(np.int64),
                0, width - 1,
            )
            bz = np.clip(
                np.rint(sample_z + sign * normal_z * step).astype(np.int64),
                0, height - 1,
            )
            run &= waterbody[bz, bx]
            if not run.any():
                break
            total += run
    return total


def local_narrow_points(width, window):
    """sample ที่แคบที่สุดในละแวกของตัวเอง — จุดที่ควรให้ผิวน้ำลดระดับ

    ผลักขั้นไปอยู่ช่วงร่องแคบ วัดจากโลกจริงแล้วว่าจุดที่ผู้เล่นบอกว่าสวย
    (6165, 6068) มีอัตราการแผ่น้ำสูงถึง 52% แต่ **ศูนย์** อยู่บนผืนน้ำกว้าง
    ขณะที่สองจุดที่มีปัญหามีการแผ่น้อยกว่ามากแต่กระจุกบนผืนกว้าง — ตัวแยกจึง
    เป็นความกว้าง ไม่ใช่การมีน้ำไหล  บนผืนกว้าง flowing block มี source ข้าง ๆ
    ครบสองตัวแล้วกลายเป็น source ใหม่ตามกฎ infinite water ระดับน้ำจึงถูกยกถาวร
    """
    width = np.asarray(width, dtype=np.int32)
    span = max(1, int(window))
    floor = ndimage.minimum_filter(width, size=2 * span + 1, mode="nearest")
    return width <= floor


def quantize_stage_pools(stage, raw_y, min_pool_samples, max_above=2,
                         step_allowed=None):
    """รวบขั้นเล็ก ๆ ให้เป็นแอ่งราบยาวสลับจุดตก (step-pool)

    ลำธารบนภูเขาจริงไม่ได้ลาดลงทีละบล็อกสม่ำเสมอ แต่เป็นแอ่งราบยาวคั่นด้วยจุด
    ตกสั้น ๆ  สูตรเดิมปล่อยให้ stage ลดทันทีที่ DEM ลด จึงได้ขั้นถี่กว่าที่
    ภูมิประเทศกำหนด (วัดได้ median แอ่ง 5 cell ขณะที่ความชันรวมบอกว่าควรเป็น 8)

    ``max_above`` กันไม่ให้การยืดแอ่งดันผิวน้ำสูงเหนือพื้นเดิมเกินไปจนน้ำล้น
    ออกข้าง — ถึงเพดานเมื่อไหร่ก็ปล่อยให้ลดตามเดิม  รับได้ทั้ง scalar และ
    array ต่อ sample: น้ำจะขังเหนือพื้นได้ก็ต่อเมื่อมีตลิ่งจริงสูงพอขังมัน
    (ดู bank_confinement) — ค่าคงที่ทั้งเส้นเคยสร้างเขื่อนเตี้ย ๆ กลางที่ราบ
    ที่ต้องพึ่งคันดินเทียมตลอดสาย
    """
    stage = np.asarray(stage, dtype=np.int32).copy()
    raw_y = np.asarray(raw_y, dtype=np.float64)
    if stage.size < 2:
        return stage
    allowed = np.broadcast_to(
        np.asarray(max_above, dtype=np.float64), stage.shape
    )
    span = max(1, int(min_pool_samples))
    # ``step_allowed`` False = ตรงนี้ลำน้ำกว้าง ให้ยืดแอ่งผ่านไปหาจุดแคบก่อน
    # เพดาน ``max_above`` ยังคุมอยู่ จึงยืดได้ไม่เกินที่ตลิ่งขังน้ำไหว
    if step_allowed is None:
        allow_step = np.ones(stage.shape, dtype=bool)
    else:
        allow_step = np.asarray(step_allowed, dtype=bool)
        if allow_step.shape != stage.shape:
            raise ValueError("step_allowed must match stage")
    anchor = 0
    for i in range(1, stage.size):
        if stage[i] >= stage[anchor]:
            stage[i] = stage[anchor]
            continue
        near_enough = (i - anchor) < span
        would_flood = stage[anchor] - raw_y[i] > allowed[i]
        hold = near_enough or not allow_step[i]
        if hold and not would_flood:
            stage[i] = stage[anchor]      # ยืดแอ่งต่อ
        else:
            anchor = i                    # ปล่อยให้ตกตรงนี้ แล้วเริ่มแอ่งใหม่
    return stage


def bank_confinement(full_terrain, sample_x, sample_z, radius):
    """ความสูงต่ำสุดของตลิ่งสองฝั่ง ที่ระยะ radius+1.5 ตั้งฉากกับเส้นลำน้ำ

    นี่คือเพดานตามธรรมชาติของการยืดแอ่ง: น้ำขังอยู่เหนือพื้นได้เท่าที่ตลิ่ง
    จริงของ DEM ขังมันไว้ วัดจากผังจริง (400 เส้น, 57k sample) มีแค่ 26.6%
    ของลำน้ำที่ตลิ่งขังน้ำได้ >=1 บล็อก — เพดานคงที่ 2 บล็อกทั้งเส้นจึงแปลว่า
    73% ของการยืดแอ่งยืนอยู่บนคันดินเทียมที่ seal_waterfall_banks ต้องก่อให้
    (น้ำลอยเหนือพื้น 30.2% ของ sample; จำกัดตามตลิ่งเหลือความเสียหายเท่ากับ
    ปิดฟีเจอร์ แต่ยังเก็บแอ่งในร่องเขาจริงไว้ครบ)
    """
    full_terrain = np.asarray(full_terrain)
    sample_x = np.asarray(sample_x, dtype=np.float64)
    sample_z = np.asarray(sample_z, dtype=np.float64)
    height, width = full_terrain.shape
    dx = np.gradient(sample_x)
    dz = np.gradient(sample_z)
    length = np.hypot(dx, dz)
    length[length == 0] = 1.0
    normal_x, normal_z = -dz / length, dx / length
    # radius รับได้ทั้งค่าเดียวและ array ต่อ sample — แม่น้ำกว้างต้องวัดตลิ่งที่
    # ขอบผืนน้ำจริง ซึ่งเปลี่ยนไปตลอดสาย ไม่ใช่ที่รัศมีคงที่ของ centerline
    offset = np.asarray(radius, dtype=np.float64) + 1.5
    confine = np.full(sample_x.shape, np.inf)
    for sign in (1.0, -1.0):
        bx = np.clip(
            np.rint(sample_x + sign * normal_x * offset).astype(np.int64),
            0, width - 1,
        )
        bz = np.clip(
            np.rint(sample_z + sign * normal_z * offset).astype(np.int64),
            0, height - 1,
        )
        confine = np.minimum(confine, full_terrain[bz, bx].astype(np.float64))
    return confine


def cardinalize_samples(x, z, values):
    """Insert one bridge cell wherever rounded samples move diagonally."""
    x = np.asarray(x, dtype=np.int32)
    z = np.asarray(z, dtype=np.int32)
    values = np.asarray(values, dtype=np.int16)
    if x.shape != z.shape or x.shape != values.shape or x.ndim != 1:
        raise ValueError("x, z, and values must be matching 1D arrays")
    if not len(x):
        return x.copy(), z.copy(), values.copy()

    out_x = [int(x[0])]
    out_z = [int(z[0])]
    out_values = [int(values[0])]
    for sample_i in range(1, len(x)):
        previous_x, previous_z = out_x[-1], out_z[-1]
        next_x, next_z = int(x[sample_i]), int(z[sample_i])
        next_value = int(values[sample_i])
        dx, dz = next_x - previous_x, next_z - previous_z
        if abs(dx) == 1 and abs(dz) == 1:
            # Alternate stair orientation so long diagonals do not acquire a
            # visibly biased saw edge.
            if sample_i % 2:
                bridge_x, bridge_z = next_x, previous_z
            else:
                bridge_x, bridge_z = previous_x, next_z
            bridge_value = int(round((out_values[-1] + next_value) / 2))
            out_x.append(bridge_x)
            out_z.append(bridge_z)
            out_values.append(bridge_value)
        if next_x != out_x[-1] or next_z != out_z[-1]:
            out_x.append(next_x)
            out_z.append(next_z)
            out_values.append(next_value)
        else:
            out_values[-1] = min(out_values[-1], next_value)
    return (
        np.asarray(out_x, dtype=np.int32),
        np.asarray(out_z, dtype=np.int32),
        np.asarray(out_values, dtype=np.int16),
    )


def limit_masked_steps(surface, mask, max_step=1, unlimited=None, floor=None):
    """Lower a wet surface to a local Lipschitz envelope on its 4-neighbor graph.

    ``unlimited`` คือ cell ที่ได้รับอนุญาตให้สูงกว่าเพื่อนบ้านเกิน ``max_step``
    ใช้กับริมน้ำตกที่ยืนยันจากภูมิประเทศจริง ถ้าไม่มีข้อยกเว้นนี้ การบังคับผิวน้ำ
    ให้เรียบจะกดยอดน้ำตกลงมาเสมอพื้นล่างจนน้ำตกหายไปทั้งแผนที่

    ``floor`` คือระดับต่ำสุดที่ยอมให้กดถึง — **จำเป็น ไม่ใช่ของเสริม**

    ฟังก์ชันนี้กดอย่างเดียวและไม่มีขอบเขต: ที่ไหนมี cell ต่ำ ๆ (ปากทะเลสาบ,
    ปลายน้ำ) มันจะไถผิวน้ำที่อยู่เหนือขึ้นไปลงมาทีละ ``max_step`` ต่อก้าว ยาว
    เท่าไรก็ได้  บนไหล่เขาที่ลำน้ำลงเร็วกว่านั้นเอง การไถจึงไม่มีวันจบ
    วัดที่ hill_junction: ก่อนเข้าฟังก์ชันนี้ผิวน้ำอยู่บน DEM พอดี (p50 0)
    ออกมาแล้วจมลง **38 บล็อก** (p90 69, สูงสุด 122) แล้ว `terrain[way] =
    surface` ก็ขุดร่องตามลงไป — นี่คือที่มาของ "ลำธารเป็นปล่องหิน" ทั้งหมด
    """
    surface = np.asarray(surface)
    mask = np.asarray(mask, dtype=bool)
    if surface.shape != mask.shape or surface.ndim != 2:
        raise ValueError("surface and mask must be matching 2D arrays")
    if unlimited is not None:
        unlimited = np.asarray(unlimited, dtype=bool)
        if unlimited.shape != mask.shape:
            raise ValueError("unlimited must match mask")
    if floor is not None:
        floor = np.asarray(floor, dtype=np.int32)
        if floor.shape != mask.shape:
            raise ValueError("floor must match mask")
    step = max(0, int(max_step))
    result = surface.astype(np.int32, copy=True)
    queue = [
        (int(result[z, x]), int(z), int(x))
        for z, x in np.argwhere(mask)
    ]
    heapq.heapify(queue)
    height, width = mask.shape
    while queue:
        value, z, x = heapq.heappop(queue)
        if value != int(result[z, x]):
            continue
        for nz, nx in ((z - 1, x), (z + 1, x), (z, x - 1), (z, x + 1)):
            if not (0 <= nz < height and 0 <= nx < width) or not mask[nz, nx]:
                continue
            if unlimited is not None and unlimited[nz, nx]:
                continue
            candidate = value + step
            if floor is not None:
                candidate = max(candidate, int(floor[nz, nx]))
            if candidate < int(result[nz, nx]):
                result[nz, nx] = candidate
                heapq.heappush(queue, (candidate, nz, nx))
    return result.astype(surface.dtype, copy=False)


def flatten_cross_sections(surface, mask, max_width=MAX_SECTION_WIDTH,
                           passes=8, locked=None, max_spread=MAX_SECTION_SPREAD,
                           propagate_locked=False, floor=None):
    """บังคับให้ผิวน้ำ *ในหน้าตัดเดียวกัน* เท่ากันทั้งเส้น

    แต่ละ cell รับระดับจาก sample ของ centerline ที่ใกล้ที่สุด พอ centerline ถูก
    แปลงเป็นบันไดแนวแกน (cardinalize_samples) cell คนละฝั่งของบันไดจะได้คนละ
    sample คนละระดับ ผลคือ **ขั้น 1 บล็อกวิ่งขนานไปกับแม่น้ำ** ไม่ใช่ตัดขวาง
    เดินเลียบฝั่งจะเห็นฝั่งหนึ่งสูงกว่าอีกฝั่งตลอดแนว และเมื่อวานิลลา update น้ำ
    ฝั่งสูงก็ไหลลงฝั่งต่ำให้เห็นชัด

    วัดจากผังจริง: 28-31% ของหน้าตัดมีผิวน้ำไม่เท่ากัน กระทบ 43-45% ของ cell

    ยุบเฉพาะ run ที่ **สั้นกว่า ``max_width``** เพราะ run สั้นในแนวแกนคือหน้าตัด
    ขวางลำน้ำ ส่วน run ยาวคือลำน้ำที่บังเอิญวิ่งขนานแกน ซึ่งห้ามยุบ ไม่งั้นทั้งสาย
    จะถูกกดลงเป็นระดับเดียว

    ใช้ค่าต่ำสุดของ run เสมอ (ไม่ใช่ค่ากลาง) เพื่อไม่ให้ผิวน้ำถูกยกขึ้นเหนือพื้น
    ที่ไหนเลย — การยกต้องอาศัยคันดิน ส่วนการกดแค่ขุดเพิ่มซึ่งไม่ต้องพึ่งอะไร
    """
    surface = np.asarray(surface, dtype=np.int16).copy()
    mask = np.asarray(mask, dtype=bool)
    if surface.shape != mask.shape:
        raise ValueError("surface and mask must have the same shape")
    limit = max(2, int(max_width))

    def level_runs(values, flags, pinned, floors=None):
        """ยุบทุก run ตามแกนแรกให้ราบ **ในที่** คืน True ถ้ามีอะไรเปลี่ยน

        ค่าที่ใช้คือค่าต่ำสุดของ run เว้นแต่ run นั้นแตะทะเลสาบ (มี cell ที่ถูก
        pin) ซึ่งต้องใช้ระดับทะเลสาบทั้ง run — ทั้งสองกรณีได้ run ที่ *ราบ*
        เหมือนกัน ต่างแค่ค่า

        run ที่รับระดับทะเลสาบไป **ถูก pin ทั้ง run** ต่อ ไม่งั้นการวนไม่ลู่เข้า:
        อีกแกนหนึ่งเห็น cell พวกนั้นเป็น cell ธรรมดา จึงกดกลับด้วยค่าต่ำสุดของ
        run ตัวเอง แล้วแกนนี้ก็ยกกลับขึ้นไปอีก สลับกันไปจนหมดจำนวนรอบแล้วคืน
        ผลที่ยังไม่นิ่ง (ผลจึงขึ้นกับ `passes` ซึ่งเป็นทางเดียวกับที่เคยทำให้
        --global เพี้ยนจาก --patch มาแล้ว)  พอ pin แผ่ตามไปด้วย เซตของ cell ที่
        ถูก pin จะโตทางเดียว ส่วน cell ที่เหลือมีแต่ถูกกดลง — ทั้งสองอย่างมี
        ขอบเขต การวนจึงหยุดเสมอ
        """
        changed = False
        for i in range(values.shape[0]):
            row = flags[i]
            if not row.any():
                continue
            idx = np.flatnonzero(row)
            breaks = np.flatnonzero(np.diff(idx) != 1)
            starts = np.concatenate(([0], breaks + 1))
            ends = np.concatenate((breaks, [idx.size - 1]))
            for a, b in zip(starts, ends):
                span = idx[a:b + 1]
                if span.size < 2 or span.size > limit:
                    continue
                # run ที่ระดับต่างกันมาก **ไม่ใช่หน้าตัด** — มันคือช่วงที่ลำน้ำ
                # วิ่งเกือบขนานแกน แล้วบังเอิญยาวไม่เกิน `limit`
                #
                # ความยาว run ใช้แยกสองอย่างนี้ไม่ได้: วัดที่ hill_junction แล้ว
                # ฟังก์ชันนี้ยุบ run ที่ต่างกันถึง **72 บล็อก** ให้เหลือค่าต่ำสุด
                # 3,578 cell ในรอบเดียว แล้ว `terrain[way] = surface` ก็ขุดตาม
                # ลงไป — นี่คือปล่องหินที่ golden patches จับได้ ไม่ใช่ envelope
                #
                # artifact ที่ตั้งใจแก้คือขั้น *1 บล็อก* ข้ามหน้าตัด การยุบอะไร
                # ที่ต่างกันมากกว่า `max_spread` จึงเกินหน้าที่ของมันเสมอ
                run = values[i, span]
                if int(run.max()) - int(run.min()) > int(max_spread):
                    continue
                target = None
                if pinned is not None:
                    held = pinned[i, span]
                    if (held != UNRESOLVED).any():
                        target = held[held != UNRESOLVED].max()
                        pinned[i, span] = target
                if target is None:
                    target = values[i, span].min()
                    # ห้ามยุบลงต่ำกว่าเพดานการขุดของ cell เอง — ไม่งั้นการยุบจะ
                    # ต่อยอดจากที่ envelope กดไว้จนแล้วจนรอด (วัดได้ว่า p90 ของ
                    # ความลึกทะลุ MAX_ENVELOPE_INCISION ไปเป็น 10 บล็อก)
                    if floors is not None:
                        target = max(target, int(floors[i, span].max()))
                if (values[i, span] != target).any():
                    values[i, span] = target
                    changed = True
        return changed

    limit_floor = None if floor is None else np.asarray(floor, dtype=np.int32)
    if limit_floor is not None and limit_floor.shape != surface.shape:
        raise ValueError("floor must match surface")
    if locked is None:
        pin = None
    else:
        # ปกติต้อง copy เพราะการแผ่ pin เขียนลงอาร์เรย์นี้
        #
        # ``propagate_locked`` ให้ผู้เรียก *เก็บ* การแผ่นั้นไว้ใช้รอบถัดไป จำเป็น
        # เมื่อถูกเรียกวนคู่กับ `limit_masked_steps`: cell ที่ถูกยกขึ้นหาระดับ
        # ทะเลสาบในรอบนี้ ถ้ารอบหน้า envelope ไม่รู้ว่ามันถูก pin ก็จะกดกลับลงมา
        # แล้วการยุบก็ยกขึ้นใหม่ วนแบบนี้ไปเรื่อย ๆ — วัดที่ flat_river: ผลสุดท้าย
        # ยังไม่นิ่ง (ยุบซ้ำเปลี่ยนอีก 141 cell) แม้วนไปแล้ว 16 รอบ
        pin = (
            np.asarray(locked, dtype=np.int16) if propagate_locked
            else np.array(locked, dtype=np.int16)
        )
        if pin.shape != surface.shape:
            raise ValueError("locked must match surface")

    # ยุบทีละแกนสลับกัน **วนจนไม่มีอะไรเปลี่ยน** ไม่ใช่คำนวณสองแกนแล้วเอา min
    #
    # เคยทำแบบ min(by_row, by_col) ด้วยเหตุผลว่า "ต้องสมมาตร ไม่งั้นแกนที่ทำ
    # ทีหลังชนะ" แต่มันผิด: เซลล์ในแถวเดียวกันได้ค่าจากคอลัมน์คนละคอลัมน์ซึ่งมี
    # ค่าต่ำสุดคนละค่า พอ min ต่อเซลล์ ผลลัพธ์ก็ไม่เท่ากันอีก — การยุบถูกทำลาย
    # ทันทีที่รวม  วัดตอนรันจริง: flatten ทำให้ row-run ที่ไม่เท่ากันเพิ่มจาก
    # 102 เป็น 111 คือแย่กว่าไม่ทำเลย
    #
    # ปัญหา "แกนสุดท้ายชนะ" แก้ด้วยการวนจนลู่เข้า ที่จุดลู่เข้าทั้งสองแกนราบพร้อมกัน
    # `surface.T` เป็น view ของ `surface` การแก้ผ่านมันจึงแก้ตัวจริงในที่
    for _ in range(max(1, int(passes))):
        changed = level_runs(surface, mask, pin, limit_floor)
        changed |= level_runs(
            surface.T, mask.T, None if pin is None else pin.T,
            None if limit_floor is None else limit_floor.T,
        )
        if not changed:
            break
    return surface


def detect_terrain_cliffs(terrain, mask, min_drop=MAX_WATERFALL_DROP,
                          side="lip"):
    """cell ริมน้ำที่ *ภูมิประเทศจริง* ตกลงอย่างน้อย ``min_drop`` ในหนึ่งก้าว

    ``side`` เลือกว่าจะทำเครื่องหมายฝั่งไหนของขอบที่ชัน
      ``"lip"``   cell บน — ฝั่งที่ envelope ต้องไม่กดลง
      ``"foot"``  cell ล่าง — ฝั่งที่ต้องมีม่านน้ำมาปิด

    **สองฝั่งนี้คนละ cell กัน** และนั่นคือบั๊กที่ `binary_dilation` เคยกลบไว้:
    เดิมฟังก์ชันนี้คืนเฉพาะ lip แต่เงื่อนไขสร้างม่าน (`strong_fall`) ไปอ่านที่
    foot การ dilate 3x3 ทำให้ทั้งสอง cell ติดธงพร้อมกันจึง "ใช้ได้" แต่แลกกับ
    การปลดล็อกให้ผิวน้ำตกไม่จำกัดในรัศมี 1 บล็อกรอบหน้าผาทุกแห่ง — ที่มาของ
    ม่านสูง 12 บล็อกข้างขั้นจริง 2 บล็อก

    นี่คือเส้นแบ่งระหว่าง "น้ำตก" กับ "ลำธารชัน" ที่ WATER_REDESIGN เรียกร้อง
    ว่าต้องมี — ห้ามเติมม่านน้ำจากทุก DEM step แต่ต้องสร้างจาก feature ที่
    ภูมิประเทศยืนยัน

    ใช้ terrain ก่อนถูก carve เป็นตัวตัดสิน เพราะหลัง carve แล้วผิวน้ำกับพื้นจะ
    เท่ากันจนแยกไม่ออกว่าเดิมชันเพราะหน้าผาหรือเพราะการปัดเศษ
    """
    terrain = np.asarray(terrain, dtype=np.int32)
    mask = np.asarray(mask, dtype=bool)
    cliff = np.zeros(mask.shape, dtype=bool)
    for dst, src in (
        (np.s_[1:, :], np.s_[:-1, :]),
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[:, 1:], np.s_[:, :-1]),
        (np.s_[:, :-1], np.s_[:, 1:]),
    ):
        drop = terrain[src] - terrain[dst]
        falling = mask[src] & mask[dst] & (drop >= int(min_drop))
        if side == "foot":
            cliff[dst] |= falling
        else:
            cliff[src] |= falling
    return cliff


def _line_slice(sources, line_i):
    offsets = sources["offsets"]
    return slice(int(offsets[line_i]), int(offsets[line_i + 1]))


def _lines_intersecting(sources, x0, x1, z0, z1):
    for line_i in range(len(sources["kind"])):
        sl = _line_slice(sources, line_i)
        x = sources["points_x"][sl]
        z = sources["points_z"][sl]
        if (
            x.max(initial=-np.inf) >= x0
            and x.min(initial=np.inf) < x1
            and z.max(initial=-np.inf) >= z0
            and z.min(initial=np.inf) < z1
        ):
            yield line_i


def _contiguous_runs(flags):
    """ช่วง [start, stop) ที่ ``flags`` เป็นจริงต่อเนื่องกัน"""
    flags = np.asarray(flags, dtype=bool)
    if not flags.any():
        return []
    padded = np.concatenate(([False], flags, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[0::2].tolist(), edges[1::2].tolist()))


# ผิวน้ำต้องต่ำกว่าตลิ่งจริงเท่านี้บนที่ราบ — 1 บล็อกพอให้ตลิ่งสูงกว่าน้ำเองโดย
# ไม่ต้องก่อคัน และไม่ทำให้ร่องลึกจนดูเป็นคลองขุด
INCISION_BLOCKS = 1
# เปอร์เซ็นไทล์ของ DEM ที่ใช้เป็นระดับผิวน้ำนิ่ง — ดู _component_standing_surface
# ต่ำพอที่จะไม่ลอยเหนือริมฝั่ง แต่ไม่ใช่ค่าต่ำสุดซึ่งจะโดน outlier ของ DEM ลาก
STANDING_LEVEL_PERCENTILE = 0.10
# ที่ราบคือที่ที่ความชันต่ำกว่านี้ (บล็อกต่อบล็อก) — ลำธารบนไหล่เขาชันกว่านี้และ
# อยู่ในร่องเขาอยู่แล้ว ไม่ต้องขุดเพิ่ม (ผู้เล่นยืนยันว่าลำธารภูเขาสวยอยู่แล้ว)
FLOODPLAIN_MAX_SLOPE = 0.6


def _incise_ceiling(full_terrain, waterbody, slope_field, sample_x, sample_z):
    """เพดานผิวน้ำสำหรับช่วงที่ราบ — คืน None เมื่อไม่มีข้อมูลพอ

    วัดตลิ่งที่ *ขอบผืนน้ำจริง* (width/2 + 1.5) ไม่ใช่ที่ radius ของ centerline
    ซึ่งบนแม่น้ำกว้างจะยังอยู่กลางน้ำและอ่านได้แค่ระดับผิวน้ำเอง
    """
    if waterbody is None or slope_field is None:
        return None
    width = channel_width(waterbody, sample_x, sample_z)
    bank = bank_confinement(
        full_terrain, sample_x, sample_z, width / 2.0
    )
    gx = np.clip(np.rint(sample_x).astype(np.int64), 0,
                 full_terrain.shape[1] - 1)
    gz = np.clip(np.rint(sample_z).astype(np.int64), 0,
                 full_terrain.shape[0] - 1)
    flat = slope_field[gz, gx] <= FLOODPLAIN_MAX_SLOPE
    ceiling = np.where(flat, bank - INCISION_BLOCKS, np.inf)
    return ceiling


def line_profile_entries(
    sources, line_i, full_terrain, upstream,
    max_drop_per_sample=MAX_WATERFALL_DROP / 2.0, waterbody=None,
    slope_field=None,
):
    """profile ของเส้นหนึ่ง — คืนเป็น list เพราะเส้นอาจวิ่งออกนอกแผนที่

    **ต้องเป็นที่เดียวที่สร้าง profile** ทั้งทางของ --global (`build_line_profiles`)
    และทางที่ `shape_waterway_patch` สร้างเอง ไฟล์นี้เคยมีบั๊กจากการเขียนสูตรซ้ำ
    สองที่มาแล้วสองรอบ (max_drop_per_sample กับ stream_radius) ซึ่งทำให้ --patch
    กับ --global ให้ลำน้ำคนละแบบโดยไม่มีอะไรฟ้อง

    เดิม sample ที่อยู่นอกแผนที่ถูก `np.clip` บีบมาไว้ที่ cell แถวขอบ ทั้ง reach
    ที่ไล่ระดับลงนอกกรอบจึงถูกยัดลงบน cell เดียวกัน แล้ว `regularize_stage` ก็
    ลากผิวน้ำตามลงไป ผลคือร่องน้ำลึกชนเพดาน clip ตลอดแนวขอบแผนที่ — วัดได้
    3,864 cell ที่ -12 เป๊ะ โดยก้อนใหญ่สุดทั้งหมดอยู่ที่ x=9998 หรือ z=0
    ตัดทิ้งแทนการบีบ แล้วแยกเป็นคนละ profile ถ้าเส้นออกนอกกรอบแล้ววกกลับเข้ามา
    """
    height, width = full_terrain.shape
    sl = _line_slice(sources, line_i)
    sx, sz = densify_polyline(
        sources["points_x"][sl], sources["points_z"][sl]
    )
    rx = np.rint(sx).astype(np.int32)
    rz = np.rint(sz).astype(np.int32)
    inside = (rx >= 0) & (rx < width) & (rz >= 0) & (rz < height)

    kind = int(sources["kind"][line_i])
    radius = stream_radius(sources["width_m"][line_i], upstream[line_i])
    entries = []
    for start, stop in _contiguous_runs(inside):
        if stop - start < 2:
            continue
        gx, gz = rx[start:stop], rz[start:stop]
        stage = regularize_stage(
            full_terrain[gz, gx],
            max_drop_per_sample=max_drop_per_sample,
            gentle_drop_per_sample=STREAM_MAX_STEP * DENSIFY_SPACING,
            cliff_drop_per_sample=cliff_drop_per_sample(),
            pool_samples=MIN_POOL_BLOCKS / DENSIFY_SPACING,
            pool_confine=bank_confinement(
                full_terrain, sx[start:stop], sz[start:stop], radius
            ),
            pool_width=(
                None if waterbody is None
                else channel_width(
                    waterbody, sx[start:stop], sz[start:stop]
                )
            ),
            incise_ceiling=_incise_ceiling(
                full_terrain, waterbody, slope_field,
                sx[start:stop], sz[start:stop],
            ),
        )
        gx, gz, stage = cardinalize_samples(gx, gz, stage)
        entries.append({
            "x": gx,
            "z": gz,
            "stage": stage,
            "kind": kind,
            "radius": radius,
            "bounds": (
                int(gx.min()), int(gx.max()) + 1,
                int(gz.min()), int(gz.max()) + 1,
            ),
        })
    return entries


def build_line_profiles(
    sources, full_terrain, max_drop_per_sample=MAX_WATERFALL_DROP / 2.0
):
    """Precompute directed raster profiles once for tiled global shaping."""
    profiles = []
    upstream = accumulate_upstream_length(sources)
    # อ่าน mask ครั้งเดียว — `sources` เป็น NpzFile การ index ทุกครั้งคือการ
    # คลาย 100 MB ใหม่ ถ้าเรียกในลูป 4,882 เส้นจะช้าจนดูเหมือนค้าง
    waterbody = np.asarray(sources["waterbody_mask"], dtype=bool)
    # ครั้งเดียวทั้งแผนที่ — คำนวณต่อเส้นคือ gradient 100M cell x 4,882 รอบ
    slope_field = hillside_rise_per_block(
        np.asarray(full_terrain), minimum=0.0
    )
    for line_i in range(len(sources["kind"])):
        profiles.extend(line_profile_entries(
            sources, line_i, full_terrain, upstream,
            max_drop_per_sample=max_drop_per_sample, waterbody=waterbody,
            slope_field=slope_field,
        ))
    return profiles


def waterway_corridor(profiles, shape, extra=0.0):
    """แถบน้ำไหลรอบ centerline — ส่วนของ waterbody polygon ที่เป็น "แม่น้ำ"

    OSM เก็บแม่น้ำกว้างเป็น polygon เดียวกับทะเลสาบ (`natural=water`) ระบบเดิม
    จึงแยกสองอย่างนี้ด้วย relief ของพื้นใต้น้ำอย่างเดียว แม่น้ำที่ไหลบนที่ราบ
    ผ่านเกณฑ์ `relief <= 6` เต็ม ๆ แล้วถูกจับเป็นน้ำนิ่ง — ผิวถูกบังคับให้แบน
    ทั้งที่มันไหล และได้ palette ก้นทะเลสาบ (วัดจากโลกจริง: ก้น "แม่น้ำ" เป็น
    cobblestone 47%) ตรงกับ root cause ข้อ 2 ใน WATER_REDESIGN

    ตัดสินที่ระดับ cell ไม่ใช่ component เพราะ 4-connected labeling รวมทะเลสาบ
    แม่น้ำ และลำธารที่แตะกันเป็นก้อนเดียว — ก้อนใหญ่สุดกิน 78% ของผืนน้ำทั้ง
    แผนที่ จึงไม่มีทางแยกประเภทได้ที่ระดับนั้น

    cell ที่อยู่ในรัศมีของ centerline คือร่องน้ำไหล ส่วนที่ไกลออกไปยังเป็นแอ่ง
    น้ำนิ่งตามเดิม ทะเลสาบที่มีแม่น้ำไหลเข้า/ออกจึงเสียเฉพาะแถบทางน้ำไป
    """
    corridor = np.zeros(shape, dtype=bool)
    if not profiles:
        return corridor
    buckets = {}
    for profile in profiles:
        reach = int(np.ceil(float(profile["radius"]) + float(extra)))
        buckets.setdefault(max(1, reach), []).append(profile)
    for reach, group in sorted(buckets.items()):
        seed = np.zeros(shape, dtype=bool)
        for profile in group:
            seed[profile["z"], profile["x"]] = True
        corridor |= ndimage.binary_dilation(
            seed, structure=ndimage.generate_binary_structure(2, 2),
            iterations=reach,
        )
    return corridor


def lake_depth_from_distance(distance, x0=0, z0=0, max_depth=31):
    """Create broad, non-concentric lake bathymetry without a max-depth slab."""
    distance = np.asarray(distance, dtype=np.float32)
    if distance.ndim != 2:
        raise ValueError("distance must be 2D")
    height, width = distance.shape
    base = 1.0 + 25.0 * (1.0 - np.exp(-distance / 58.0))
    influence = np.clip((distance - 3.0) / 20.0, 0.0, 1.0)
    broad = S.smooth_noise(
        int(x0), int(z0), (width, height), 110.0, 6203, 3
    ).T
    medium = S.smooth_noise(
        int(x0), int(z0), (width, height), 38.0, 6317, 2
    ).T
    relief = (3.5 * broad + 1.8 * medium) * influence
    return np.clip(np.rint(base + relief), 1, int(max_depth)).astype(np.uint8)


def waterway_depth_from_banks(way, kind_depth, x0=0, z0=0, water=None):
    """ก้นลำน้ำที่ลาดจากตลิ่งลงหาร่องลึก แทนรางสี่เหลี่ยมก้นแบน

    เดิมความลึกเป็นค่าคงที่ต่อชนิด (river=3 ทั้งสาย) ผลคือแม่น้ำกว้าง 10-12
    บล็อกกลายเป็นรางก้นแบนลึกเท่ากันหมดทั้งหน้าตัด — วัดจากผังจริง ก้นแม่น้ำ
    ที่ราบมีขั้นเพียง 4% ของ cell ขณะที่ลำธารภูเขาซึ่งดูเป็นธรรมชาติมี 39%
    นั่นคือความ "เหลี่ยม" ที่ผู้เล่นเห็น ไม่ใช่ระดับผิวน้ำ (profile ของแม่น้ำ
    ที่ราบมีขั้นแค่ 5 ครั้งตลอด 1,090 บล็อก แอ่งยาว 212 บล็อก อยู่แล้ว)

    วิธีเดียวกับที่ทะเลสาบใช้อยู่แล้ว (`lake_depth_from_distance`): ไล่ความลึก
    ตามระยะจากขอบน้ำ แล้วบวก noise พิกัดโลกให้ร่องลึกส่ายไปมาแบบ thalweg จริง
    noise ยึดพิกัดโลกจึงไม่มีรอยต่อระหว่าง tile
    """
    way = np.asarray(way, dtype=bool)
    kind_depth = np.asarray(kind_depth, dtype=np.float32)
    if kind_depth.shape != way.shape:
        raise ValueError("kind_depth must match way")
    depth = np.zeros(way.shape, dtype=np.uint8)
    if not way.any():
        return depth

    height, width = way.shape
    # ระยะต้องวัดถึง **พื้นดินจริง** ไม่ใช่ถึงขอบของแถบ `way`
    #
    # แม่น้ำที่ไหลเลียบหรือไหลเข้าทะเลสาบมีแถบ way แคบ ทั้งที่ผืนน้ำตรงนั้นกว้าง
    # มาก ถ้าวัดจาก way อย่างเดียวจะได้ระยะ 1-2 แล้วให้ความลึกแค่ 1-2 บล็อก
    # ทั้งที่ควรลึกเท่าผืนน้ำรอบตัว
    reference = way if water is None else np.asarray(water, dtype=bool)
    edge = ndimage.distance_transform_edt(reference).astype(np.float32)
    # ความลึกต้องมาจาก **ความกว้างจริง** ไม่ใช่ชนิดที่ OSM แท็กไว้
    #
    # เดิมใช้ชนิดล้วน (river=3, stream=1) วัดจากผังจริงแล้ว 786,607 cell เป็น
    # `stream` จึงลึก 1 ต่อให้กว้างแค่ไหน และ `ditch` กว้างถึง 14 บล็อกแต่ลึก
    # สูงสุด 2  ผลคือแม่น้ำกว้าง 46% ลึกเพียง 1 บล็อก — มองทะลุเห็นท้องน้ำตลอด
    # อ่านเป็นแอ่งโคลนมากกว่าแม่น้ำ
    #
    # `edge` คือระยะถึงฝั่งที่ใกล้ที่สุด กลางลำน้ำจึงเท่ากับครึ่งความกว้างพอดี
    # ใช้มันเป็นความลึกเป้าหมายโดยตรง ลำธารกว้าง 1 บล็อกได้ 1 เท่าเดิม ส่วน
    # แม่น้ำกว้าง 10 บล็อกได้ถึงเพดาน  ชนิดของ OSM เหลือเป็นแค่พื้นขั้นต่ำ
    target = np.minimum(
        np.maximum(edge, np.minimum(kind_depth, edge)),
        float(WATERWAY_MAX_DEPTH),
    )
    ramp = np.clip(edge / np.maximum(1.0, target), 0.0, 1.0)
    kind_depth = target
    # ร่องลึกไม่ได้อยู่กลางเป๊ะ ๆ มันแกว่งไปมาตามโค้ง
    broad = S.smooth_noise(int(x0), int(z0), (width, height), 44.0, 5171, 2).T
    fine = S.smooth_noise(int(x0), int(z0), (width, height), 15.0, 5233, 2).T
    relief = (0.9 * broad + 0.45 * fine) * ramp
    value = np.rint(kind_depth * ramp + relief)
    # ริมตลิ่งต้องตื้นเสมอ ไม่งั้นได้ผนังตั้งใต้น้ำรอบลำน้ำเหมือนเดิม
    value = np.clip(value, 1.0, kind_depth + 1.0)
    depth[way] = value[way].astype(np.uint8)
    return depth


def shape_standing_water_patch(
    base_y, sources, x0, x1, z0, z1, bank_width=BANK_BLEND_BLOCKS,
    distance_pad=256,
    standing_surface=None,
):
    """Flatten standing-water components, form banks, and carve bathymetry."""
    base_y = np.asarray(base_y, dtype=np.int16)
    expected = (z1 - z0, x1 - x0)
    if base_y.shape != expected:
        raise ValueError("base_y shape must match patch bounds")
    full_body = sources["waterbody_mask"]
    body = full_body[z0:z1, x0:x1].astype(bool)
    if standing_surface is not None:
        supplied = np.asarray(standing_surface, dtype=np.int16)
        body = supplied != UNRESOLVED
    if standing_surface is None:
        labels, count = ndimage.label(
            body, structure=ndimage.generate_binary_structure(2, 1)
        )
        surface = np.full(expected, UNRESOLVED, dtype=np.int16)
        component_levels = np.zeros(count + 1, dtype=np.int16)
        for component_i in range(1, count + 1):
            component = labels == component_i
            values = base_y[component].astype(np.int32)
            low = int(values.min())
            level = low + int(np.bincount(values - low).argmax())
            component_levels[component_i] = level
            surface[component] = level
    else:
        standing_surface = np.asarray(standing_surface, dtype=np.int16)
        if standing_surface.shape != expected:
            raise ValueError("standing_surface shape must match patch bounds")
        labels = np.zeros(expected, dtype=np.int32)
        component_levels = np.zeros(1, dtype=np.int16)
        surface = np.full(expected, UNRESOLVED, dtype=np.int16)
        surface[body] = standing_surface[body]

    pad = max(1, int(distance_pad))
    px0, px1 = max(0, x0 - pad), min(full_body.shape[1], x1 + pad)
    pz0, pz1 = max(0, z0 - pad), min(full_body.shape[0], z1 + pad)
    context = full_body[pz0:pz1, px0:px1].astype(bool)
    if context.all():
        # EDT needs an explicit dry exterior. This is normally only relevant
        # for a patch wholly inside a very large mapped water polygon.
        context = np.pad(context, 1, constant_values=False)
        distance = ndimage.distance_transform_edt(context)[
            1 + z0 - pz0:1 + z1 - pz0,
            1 + x0 - px0:1 + x1 - px0,
        ]
    else:
        distance = ndimage.distance_transform_edt(context)[
            z0 - pz0:z1 - pz0,
            x0 - px0:x1 - px0,
        ]
    depth = lake_depth_from_distance(distance, x0=x0, z0=z0)
    depth[~body] = 0

    terrain = base_y.astype(np.int32, copy=True)
    terrain[body] = surface[body].astype(np.int32)
    if body.any() and bank_width > 0:
        bank_distance, indices = ndimage.distance_transform_edt(
            ~body, return_indices=True
        )
        near_stage = surface[tuple(indices)].astype(np.int32)
        # ยกเฉพาะเท่าที่กันน้ำรั่วแล้วเฟดกลับ — ไม่สร้างสันเหนือผิวน้ำ และไม่มี
        # ขอบคมที่ระยะใดระยะหนึ่ง หน้าผาธรรมชาติที่สูงอยู่แล้วไม่ถูกแตะ
        terrain = taper_bank_lift(
            terrain, near_stage, bank_distance,
            blend_blocks=bank_width, where=~body,
        )
        # แล้วลาดขอบลงหาน้ำด้วยกฎเดียวกับลำธาร ไม่งั้นทะเลสาบจะเหลือผนังตั้ง
        # รอบขอบ (วัดได้ 41.8% ของบล็อกที่ติดทะเลสาบ ตอนที่ยังมีแต่การยก)
        terrain = taper_bank_cut(
            terrain, near_stage, bank_distance,
            blend_blocks=bank_width, where=~body,
            rise_per_block=hillside_rise_per_block(base_y),
        )
        # ขอบทะเลสาบมีหน้าผาจริงเยอะกว่าลำธาร จึงจำกัดการขยับให้แคบกว่า
        original = base_y.astype(np.int32, copy=False)
        np.clip(
            terrain,
            original - MAX_LAKE_BANK_ADJUST,
            original + MAX_LAKE_BANK_ADJUST,
            out=terrain, where=~body,
        )

        # ---- ชายฝั่งทะเลสาบ ----
        #
        # หลักการเดียวกับลำน้ำ (ดู channel_sections): วงแรกรอบผืนน้ำต้องเป็นที่ราบ
        # ที่เหยียบได้ ไม่ใช่ผนังตั้งชนน้ำ  ผู้ใช้ชี้จากภาพในเกมว่าลำน้ำเป็นรอยผ่า
        # ส่วนที่ `lake_mouth` วัดได้ว่าขอบน้ำ **57% เป็นผนังตั้ง** ชายฝั่งแค่ 43%
        #
        # ต้องมา *หลัง* clip เพราะชายฝั่งเป็นส่วนหนึ่งของผืนน้ำ ไม่ใช่การขยับตลิ่ง
        # จึงไม่อยู่ใต้เพดาน MAX_LAKE_BANK_ADJUST เหมือนกัน (เหตุผลเดียวกับที่
        # ลำน้ำแยก SHORE_MAX_CUT ออกจาก BANK_MAX_CUT)
        ring = ndimage.binary_dilation(
            body, structure=ndimage.generate_binary_structure(2, 2),
            iterations=CS.SHORE_BLOCKS,
        ) & ~body
        if ring.any():
            beach = near_stage + CS.SHORE_RISE
            current = terrain[ring]
            target = beach[ring]
            terrain[ring] = np.where(
                current > target,
                np.maximum(target, current - CS.SHORE_MAX_CUT),
                np.maximum(
                    current, np.minimum(target, current + CS.SEAL_MAX_RAISE)
                ),
            )

    return {
        "terrain_y": terrain.astype(np.int16),
        "standing_water_mask": body,
        "surface_y": surface,
        "depth": depth,
        "standing_component": labels.astype(np.int32),
        "component_levels": component_levels,
    }


USE_SECTION_CHANNEL = True


def shape_waterway_sections(
    base_y, sources, x0, x1, z0, z1, line_profiles=None,
    standing_mask=None, max_surface_step=MAX_WATERFALL_DROP,
):
    """ขึ้นรูปลำน้ำจากหน้าตัดที่ประกาศไว้ (ดู channel_sections.py)

    คืน dict คีย์ชุดเดียวกับ `shape_waterway_patch` เป๊ะ ๆ เพื่อให้ผู้บริโภคทุกตัว
    (build_terrain / paint_surface / report) ไม่ต้องแก้อะไรเลย

    ต่างจากของเดิมตรงที่ **ไม่มีขั้นตอนซ่อมสักขั้น**: ไม่มี envelope บน raster
    ไม่มีการยุบหน้าตัด ไม่มีการตอกปากน้ำ ไม่มีเพดานการขุดกระจายสามที่ ทุกอย่าง
    ถูกกำหนดตอนวางหน้าตัด
    """
    base_y = np.asarray(base_y, dtype=np.int16)
    expected = (z1 - z0, x1 - x0)
    if base_y.shape != expected:
        raise ValueError("base_y shape must match patch bounds")

    source_body = sources["waterbody_mask"][z0:z1, x0:x1].astype(bool)
    body = (
        source_body if standing_mask is None
        else np.asarray(standing_mask, dtype=bool)
    )
    flowing_body = source_body & ~body
    source_kind = sources["waterway_kind"][z0:z1, x0:x1]

    if line_profiles is None:
        full_terrain = np.load(
            os.path.join(HERE, "terrain_y.npy"), mmap_mode="r"
        )
        upstream = accumulate_upstream_length(sources)
        full_waterbody = np.asarray(sources["waterbody_mask"], dtype=bool)
        full_slope = hillside_rise_per_block(
            np.asarray(full_terrain), minimum=0.0
        )
        profiles = []
        for line_i in _lines_intersecting(sources, x0 - 2, x1 + 2, z0 - 2, z1 + 2):
            profiles.extend(line_profile_entries(
                sources, line_i, full_terrain, upstream,
                waterbody=full_waterbody, slope_field=full_slope,
            ))
    else:
        profiles = line_profiles

    # ความชันไหล่เขาของ patch นี้ — ตัวกำหนดว่าตลิ่งต้องลาดแค่ไหนถึงจะกลืน
    slope_field = hillside_rise_per_block(base_y, minimum=0.0)
    # พื้นดินที่ขอบนอกของแถบตลิ่ง — คำนวณครั้งเดียวต่อ patch แล้วใช้ร่วมทุกเส้น
    outer_rim = CS.outer_rim_field(base_y.astype(np.int32))
    local = []
    for profile in profiles:
        bx0, bx1, bz0, bz1 = profile["bounds"]
        if bx1 <= x0 - 2 or bx0 >= x1 + 2 or bz1 <= z0 - 2 or bz0 >= z1 + 2:
            continue
        shifted = {
            "x": np.asarray(profile["x"], dtype=np.int32) - x0,
            "z": np.asarray(profile["z"], dtype=np.int32) - z0,
            "stage": profile["stage"],
            "radius": profile["radius"],
            "kind": profile["kind"],
        }
        plan = CS.plan_from_profile(
            shifted, base_y.astype(np.int32), slope_field, outer_rim=outer_rim,
        )
        if plan is not None:
            local.append(plan)

    # ---- ปากน้ำ: ลำธารต้องลงไปถึงระดับทะเลสาบจริง ----
    #
    # โครงสร้างเดิมทำด้วยการ "ตอก" ค่าลงบน raster แล้วต้องมีกฎยกเว้นตามมาอีกชุด
    # ที่นี่มันเป็นแค่การแก้ stage ของ sample ท้าย ๆ ก่อนวางหน้าตัด — หน้าตัดที่
    # ถูกวางจึงต่อเนื่องกับทะเลสาบตั้งแต่ต้น ไม่มีอะไรต้องซ่อมทีหลัง
    if body.any():
        lake_level = np.where(body, base_y.astype(np.int32), UNRESOLVED)
        near_lake = ndimage.maximum_filter(lake_level, size=7)
        touches = ndimage.binary_dilation(
            body, structure=ndimage.generate_binary_structure(2, 2),
            iterations=3,
        )
        for plan in local:
            at_lake = touches[plan.z, plan.x]
            if not at_lake.any():
                continue
            target = near_lake[plan.z, plan.x]
            valid = at_lake & (target != UNRESOLVED)
            if not valid.any():
                continue
            # ลงได้อย่างเดียว ไม่ยกขึ้น แล้วไล่ให้ช่วงเหนือขึ้นไปลาดตามอย่างต่อเนื่อง
            plan.stage[valid] = np.minimum(plan.stage[valid], target[valid])
            for i in range(len(plan) - 1, 0, -1):
                plan.stage[i - 1] = min(
                    int(plan.stage[i - 1]),
                    int(plan.stage[i]) + int(MAX_WATERFALL_DROP),
                )

    stamped = CS.stamp_sections(local, base_y.astype(np.int32), protect=body)
    way = stamped["water"] & ~body
    terrain = stamped["terrain"]
    surface = np.where(way, stamped["surface"], UNRESOLVED).astype(np.int16)
    depth = np.where(way, stamped["depth"], 0).astype(np.uint8)
    kind = np.where(way, stamped["kind"], 0).astype(np.uint8)

    # ผืนน้ำกว้างที่ OSM แมปไว้แต่อยู่ไกล centerline ใช้ terrain ท้องถิ่นเป็นผิวน้ำ
    # (LiDAR ยิงไม่ทะลุน้ำ ค่าที่อ่านได้จึงเป็นผิวน้ำอยู่แล้ว) เหมือนของเดิม
    # `flowing_body` = polygon ของ OSM ที่ไม่ผ่านเกณฑ์น้ำนิ่ง
    #
    # วัดทั้งแผนที่แล้วได้ **0 cell** — polygon ทุกผืนถูกจัดเป็นน้ำนิ่งหมด เคยเขียน
    # template แยกสำหรับผืนพวกนี้แล้วพบว่าไม่เคยถูกเรียกเลย จึงถอดออกและเหลือ
    # ทางสำรองสั้น ๆ ไว้เผื่อข้อมูลเปลี่ยน (ระดับ = terrain ท้องถิ่น เพราะ LiDAR
    # ยิงไม่ทะลุน้ำ ค่าที่อ่านได้จึงเป็นผิวน้ำอยู่แล้ว)
    wide = flowing_body & ~way & ~body
    if wide.any():
        way = way | wide
        surface = np.where(wide, base_y, surface).astype(np.int16)
        depth = np.where(wide, np.maximum(depth, 1), depth).astype(np.uint8)
        kind = np.where(
            wide, np.where(source_kind > 0, source_kind, 1), kind
        ).astype(np.uint8)
        terrain = np.where(wide, base_y.astype(np.int32) - 1, terrain)

    # ---- น้ำตก ----
    # ในโครงสร้างนี้ขั้นใหญ่มาจาก profile เท่านั้น (ซึ่งปลดล็อกเฉพาะที่หน้าผาจริง)
    # จึงไม่ต้องมี mask ยกเว้นสองชุดที่เคยไม่ตรงกันอีก — **ทุกขั้น >= 3 ได้ม่าน**
    waterfall_lip = np.zeros(expected, dtype=bool)
    waterfall_foot = np.zeros(expected, dtype=bool)
    waterfall_drop = np.zeros(expected, dtype=np.uint8)
    for dst, src in (
        (np.s_[1:, :], np.s_[:-1, :]),
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[:, 1:], np.s_[:, :-1]),
        (np.s_[:, :-1], np.s_[:, 1:]),
    ):
        connected = way[dst] & way[src]
        drop = surface[src].astype(np.int32) - surface[dst].astype(np.int32)
        falling = connected & (drop >= 3)
        waterfall_lip[src] |= falling
        waterfall_foot[dst] |= falling
        waterfall_drop[dst] = np.maximum(
            waterfall_drop[dst], np.where(falling, drop, 0).astype(np.uint8)
        )
    waterfall_top = np.full(expected, UNRESOLVED, dtype=np.int16)
    waterfall_top[waterfall_foot] = (
        surface[waterfall_foot].astype(np.int32)
        + waterfall_drop[waterfall_foot].astype(np.int32)
    ).astype(np.int16)
    waterfall_pool = (
        ndimage.binary_dilation(
            waterfall_foot, structure=ndimage.generate_binary_structure(2, 1)
        ) & way & ~waterfall_lip
    )
    depth = np.where(
        waterfall_pool, np.maximum(depth, 3), depth
    ).astype(np.uint8)
    terrain = np.where(way, surface.astype(np.int32) - depth, terrain)

    return {
        "terrain_y": terrain.astype(np.int16),
        "waterway_mask": way,
        "surface_y": surface,
        "depth": depth,
        "centerline_y": np.where(
            stamped["centerline"] & way, surface, UNRESOLVED
        ).astype(np.int16),
        # หน้าตัดที่เป็นเจ้าของ cell — harness ใช้ตรวจว่าหน้าตัดเดียวกันผิวน้ำ
        # เท่ากันจริงไหม *หลัง* ด่านที่แก้ผิวน้ำตามหลัง (weir, mouth, seal,
        # ผสมทะเลสาบ) ถ้าไม่บอกไว้ ตัววัดต้องเดาด้วย EDT แล้วจับผิดตัวบนเส้นทแยง
        "section_id": np.where(way, stamped["section"], 0).astype(np.int32),
        "waterway_kind": kind,
        "waterfall_lip_mask": waterfall_lip,
        "waterfall_foot_mask": waterfall_foot,
        "waterfall_drop": waterfall_drop,
        "waterfall_pool_mask": waterfall_pool,
        "waterfall_top_y": waterfall_top,
    }


def shape_waterway_patch(
    base_y, sources, x0, x1, z0, z1, bank_width=BANK_BLEND_BLOCKS,
    line_profiles=None,
    standing_mask=None, max_surface_step=MAX_WATERFALL_DROP,
    max_channel_adjust=12,
):
    """Return centerline-driven river stage, depth, and reshaped patch terrain."""
    base_y = np.asarray(base_y, dtype=np.int16)
    expected = (z1 - z0, x1 - x0)
    if base_y.shape != expected:
        raise ValueError("base_y shape must match patch bounds")

    source_body = sources["waterbody_mask"][z0:z1, x0:x1].astype(bool)
    body = (
        source_body
        if standing_mask is None
        else np.asarray(standing_mask, dtype=bool)
    )
    flowing_body = source_body & ~body
    center = np.full(expected, UNRESOLVED, dtype=np.int16)
    center_kind = np.zeros(expected, dtype=np.uint8)
    center_radius = np.zeros(expected, dtype=np.float32)

    if line_profiles is None:
        full_terrain = np.load(
            os.path.join(HERE, "terrain_y.npy"), mmap_mode="r"
        )
        profiles = []
        # ต้องสะสมจากเครือข่ายทั้งหมด ไม่ใช่เฉพาะเส้นที่ตัดผ่าน patch ไม่งั้น
        # ลำน้ำเดียวกันจะกว้างไม่เท่ากันเมื่อดูคนละ patch
        upstream = accumulate_upstream_length(sources)
        full_waterbody = np.asarray(sources["waterbody_mask"], dtype=bool)
        full_slope = hillside_rise_per_block(
            np.asarray(full_terrain), minimum=0.0
        )
        for line_i in _lines_intersecting(
            sources, x0 - 2, x1 + 2, z0 - 2, z1 + 2
        ):
            profiles.extend(line_profile_entries(
                sources, line_i, full_terrain, upstream,
                waterbody=full_waterbody, slope_field=full_slope,
            ))
    else:
        profiles = line_profiles
    for profile in profiles:
        bx0, bx1, bz0, bz1 = profile["bounds"]
        if bx1 <= x0 - 2 or bx0 >= x1 + 2 or bz1 <= z0 - 2 or bz0 >= z1 + 2:
            continue
        gx, gz, stage = profile["x"], profile["z"], profile["stage"]
        inside = (gx >= x0) & (gx < x1) & (gz >= z0) & (gz < z1)
        lx, lz = gx[inside] - x0, gz[inside] - z0
        values = stage[inside]
        line_kind = profile["kind"]
        radius = profile["radius"]
        for px, pz, value in zip(lx, lz, values):
            old = int(center[pz, px])
            if old == UNRESOLVED or value < old:
                center[pz, px] = value
                center_kind[pz, px] = line_kind
            center_radius[pz, px] = max(center_radius[pz, px], radius)

    has_center = center != UNRESOLVED
    source_kind = sources["waterway_kind"][z0:z1, x0:x1]
    if has_center.any():
        distance, indices = ndimage.distance_transform_edt(
            ~has_center, return_indices=True
        )
        nearest_stage = center[tuple(indices)]
        nearest_kind = center_kind[tuple(indices)]
        nearest_radius = center_radius[tuple(indices)]
        in_corridor = distance <= nearest_radius
        way = (in_corridor | flowing_body) & ~body
        # ระดับผิวน้ำแผ่ออกจากร่องได้ไกลกว่าตัวร่องเอง เพื่อให้ผืนน้ำกว้างที่
        # OSM แมปไว้มีผิว *ราบ* เท่ากับร่อง ไม่ใช่ไปเกาะ DEM ดิบซึ่งมี noise
        # +-1 บล็อก  noise นั้นทำให้เกิดขั้นกระจายทั่วผืน แล้ววานิลลาก็แผ่น้ำ
        # ทับกันจนกลายเป็น source ใหม่ ระดับน้ำถูกยกถาวร
        #
        # วัดจากผังจริง: 78% ของ cell ที่น้ำแผ่ทับบนผืนกว้างคือ cell นอกร่องที่
        # รับ DEM ดิบ และ 83% ของพวกนั้นอยู่ห่างร่างไม่เกิน 2 บล็อก (p90 = 4)
        # ระยะ 5 จึงครอบคลุม ~91% โดย radius สูงสุด 2.5 + 5 = 7.5 < GLOBAL_HALO
        # ยังปลอดภัยกับการแบ่ง tile
        stage_reach = nearest_radius + LATERAL_STAGE_REACH
        # `flowing_body` คือส่วนของ polygon น้ำ OSM ที่ไม่ผ่านเกณฑ์น้ำนิ่ง cell
        # พวกนี้อยู่ใน `way` ได้โดยไม่มี centerline อยู่ใกล้เลย และเดิมมันรับ
        # `nearest_stage` จาก EDT ซึ่ง **ไม่จำกัดระยะ** — วัดที่ (2273, 3155) แล้ว
        # centerline ที่ใกล้ที่สุดอยู่ห่าง 136 บล็อกและอยู่คนละระดับความสูง ผลคือ
        # ผืนน้ำกว้างถูกกดลงจนชนเพดาน clip พอดี (DEM 84-85 -> ผิวน้ำ 73 = -12)
        # แล้วระบบก็สร้างผนังน้ำ 12 บล็อกมาเชื่อมกับทะเลสาบข้าง ๆ ที่ 85
        # ทั้งแผนที่มี 7,459 cell (0.7% ของ way) นั่งอยู่ที่พื้นเพดาน -12 เป๊ะ ๆ
        #
        # นอกร่องน้ำให้ใช้ terrain ท้องถิ่นแทน ซึ่งสำหรับผืนน้ำกว้างที่ OSM แมปไว้
        # ค่านั้น *คือ* ผิวน้ำอยู่แล้ว — LiDAR ยิงไม่ทะลุน้ำ เหตุผลเดียวกับที่
        # ทะเลสาบใช้ DEM เป็นระดับผิว
        outside = way & (distance > stage_reach)
        nearest_stage = np.where(
            outside, base_y, nearest_stage
        ).astype(np.int16)
        nearest_kind = np.where(
            outside,
            np.where(source_kind > 0, source_kind, 1),
            nearest_kind,
        ).astype(np.uint8)
    else:
        nearest_stage = np.full(expected, UNRESOLVED, dtype=np.int16)
        nearest_stage[flowing_body] = base_y[flowing_body]
        nearest_kind = np.where(
            flowing_body,
            np.where(source_kind > 0, source_kind, 1),
            0,
        ).astype(np.uint8)
        way = flowing_body.copy()

    surface = np.full(expected, UNRESOLVED, dtype=np.int16)
    surface[way] = nearest_stage[way]
    # ขอบล่างของ clip ต้องเป็นเพดานการขุดตัวเดียวกัน ไม่ใช่ `max_channel_adjust`
    #
    # `max_channel_adjust` (12) คุม *การขยับตลิ่ง* ไม่ใช่ความลึกของร่องน้ำ ใช้มัน
    # เป็นขอบล่างของผิวน้ำด้วยจึงเปิดให้ร่องลึกได้ถึง 12 บล็อกตั้งแต่ก่อนเข้า
    # envelope — วัดที่ (6448, 2304) ซึ่งสุ่มเจอว่าเป็นหุบ 66.9%: centerline อยู่
    # ต่ำกว่า DEM p50 **12 พอดี** คือชนขอบล่างนี้เป๊ะ ๆ ทั้งสาย
    lower = base_y.astype(np.int32) - int(MAX_ENVELOPE_INCISION)
    upper = base_y.astype(np.int32) + int(max_channel_adjust)
    surface[way] = np.clip(
        surface[way].astype(np.int32), lower[way], upper[way]
    ).astype(np.int16)
    # เพดานการขุดต้องคุม **ทุก** จุดที่เรียก envelope ไม่ใช่เฉพาะในลูปท้าย
    #
    # การเรียกครั้งนี้เคยไม่มีเพดาน มันจึงไถผิวน้ำลงได้ไม่จำกัดที่ 4 บล็อกต่อก้าว
    # ตั้งแต่ก่อนเข้าลูป — วัดที่ (6448, 2304) ซึ่งสุ่มเจอว่าเป็นหุบ 66.9%:
    # ผิวน้ำต่ำกว่า DEM p50 12 / p90 27 ทั้งที่ MAX_ENVELOPE_INCISION = 6
    incision_floor = base_y.astype(np.int32) - int(MAX_ENVELOPE_INCISION)
    surface = limit_masked_steps(
        surface, way, max_step=max_surface_step, floor=incision_floor,
    )
    mouth_level = np.full(expected, UNRESOLVED, dtype=np.int16)
    for dst, src in (
        (np.s_[1:, :], np.s_[:-1, :]),
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[:, 1:], np.s_[:, :-1]),
        (np.s_[:, :-1], np.s_[:, 1:]),
    ):
        # `base_y` ที่เข้ามาจาก shape_hydrology_patch คือ terrain หลังขึ้นรูป
        # น้ำนิ่งแล้ว ซึ่ง `terrain[body] = surface[body]` ค่าที่อ่านได้จึงเป็น
        # ระดับผิวทะเลสาบที่แบนแล้วจริง ๆ ไม่ใช่ DEM ดิบ
        contact = way[dst] & body[src]
        candidate = np.where(contact, base_y[src], UNRESOLVED)
        mouth_level[dst] = np.maximum(mouth_level[dst], candidate)
    # ปากน้ำต้องต่อเนื่องกับทะเลสาบ ไม่ใช่ค้างอยู่เหนือมัน
    #
    # เดิม pin เฉพาะเมื่อต่างกันไม่เกิน max_surface_step ผลคือลำธารที่ค้างสูงกว่า
    # ทะเลสาบมากกว่านั้น **ไม่ถูกดึงลงเลย** แล้วระบบไปเสกม่านน้ำมาปิดช่องแทน
    # วัดแล้วบน patch (2242, 3057): ม่านทั้ง 117 เสาที่นั่นหายหมดเมื่อเลิกเสก
    # แปลว่าไม่มีเสาใดมีหน้าผารองรับ — มันคือลำธารที่ไม่ไหลลง ไม่ใช่น้ำตก
    # นี่คือการละเมิดกฎ "น้ำไหลต้องไหลลงเสมอ" ไม่ใช่เรื่องของฟีเจอร์น้ำตก
    #
    # ปรับทั้งสองทิศอย่างสมมาตร จำกัดที่ mouth_pin_max_drop เพื่อคุมระยะที่
    # envelope ต้องแพร่
    #
    # การ *ยก* เคยจำกัดไว้ที่ max_surface_step (4) เพราะกลัวว่าการยกผิวน้ำเหนือ
    # พื้นเดิมจะสร้างกำแพงหินริมทะเลสาบ ผลคือทางออกทะเลสาบที่ลำธารอยู่ต่ำกว่า
    # มากกว่านั้นไม่ถูกยกเลย แล้ว `at_lake` ก็เสกผนังน้ำมาเชื่อมแทน — วัดได้ 40
    # เสาที่สูง >=8 บล็อกโดยไม่มีหน้าผารองรับสักต้นเดียว
    #
    # ยกให้ถึงระดับทะเลสาบแล้วปล่อยให้ envelope ไถลงทีละ UNSUPPORTED_MAX_STEP
    # ไปทางท้ายน้ำ จะได้ลาดน้ำตกสั้น ๆ ต่อเนื่องแทนผนังตั้ง ส่วนผนังข้างที่ต้องมี
    # `seal_waterfall_banks` สร้างให้อยู่แล้ว
    has_mouth = mouth_level != UNRESOLVED
    mouth_drop = np.where(
        has_mouth, surface.astype(np.int32) - mouth_level.astype(np.int32), 0
    )
    pull_down = has_mouth & (mouth_drop > 0)
    lift = has_mouth & (mouth_drop < 0)
    mouth_target = surface.astype(np.int32)
    mouth_target[pull_down] -= np.minimum(
        mouth_drop[pull_down], mouth_pin_max_drop()
    )
    mouth_target[lift] += np.minimum(
        -mouth_drop[lift], mouth_pin_max_drop()
    )
    mouth = pull_down | lift
    # การตอกปากน้ำก็ต้องอยู่ใต้เพดานการขุดเดียวกัน
    #
    # เดิมมันดึงลงได้อีก 12 บล็อกจาก *ค่าปัจจุบัน* ซึ่งอยู่ที่เพดานอยู่แล้ว รวมเป็น
    # 18 บล็อกใต้ DEM — วัดที่ (6448, 2304): cell ที่ถูกนับเป็นหุบ 1,773 cell
    # เป็นลำน้ำทั้งหมด และผิวน้ำต่ำกว่า DEM p50 17 พอดี
    # ช่องว่างที่เกิดจากการไม่ดึงลงสุดตอนนี้มีม่านน้ำปิดให้แล้ว (ดู exempt_lip)
    mouth_target = np.maximum(mouth_target, incision_floor)
    surface[mouth] = mouth_target[mouth].astype(np.int16)
    # envelope ยังคุมลำน้ำปกติไว้ที่ max_surface_step แต่ต้องยกเว้นริมหน้าผาจริง
    # ไม่งั้นน้ำตกที่ profile ปลดล็อกไว้จะถูกไถกลับเป็นขั้นเท่า ๆ กันเรียงกัน
    # (วัดแล้ว: หน้าผา 20 บล็อกกลายเป็นขั้น 4 ห้าขั้น ทำให้ drop>=3 เพิ่มสามเท่า)
    #
    # ใช้ max_step เดิมโดยไม่ลดลง เพราะระยะที่ envelope แพร่คือ excess/step การ
    # ลด step ทำให้แพร่ไกลจน halo ของ tile ไม่พอ แล้ว --global เพี้ยนจาก --patch
    # ต้องเป็น mask เดียวกับที่อนุญาตให้มีม่านน้ำข้างล่าง มิฉะนั้นจะมีจุดที่ผิวน้ำ
    # ตกแรงได้แต่ไม่มีม่านมารองรับ = ช่องว่างกลางสายน้ำ (เคยวัดได้ drop 12 บล็อก
    # ขณะที่ม่านสูงสุดแค่ 4 เพราะสองเงื่อนไขใช้ min_drop คนละค่า)
    #
    # หน้าผาจริงแค่ 3 บล็อกเคยปลดล็อกให้ผิวน้ำตกได้ *ไม่จำกัด* ในรัศมี 1 บล็อก
    # รอบตัว (dilation) จึงเกิดม่านสูง 12 บล็อกข้างขั้นจริง 2 บล็อก — ดู
    # ENABLE_WATERFALL_CURTAINS
    supported = terrain_cliff_support(base_y, way, side="lip")
    supported_foot = terrain_cliff_support(base_y, way, side="foot")
    # clip ต้องมา *ก่อน* envelope รอบสุดท้าย — ถ้า clip ทีหลัง มันจะสร้าง drop
    # ใหม่ที่ envelope ไม่ได้คุมและ detection ข้างล่างไม่ได้เตรียมม่านไว้ให้
    surface[way] = np.clip(
        surface[way].astype(np.int32), lower[way], upper[way]
    ).astype(np.int16)
    # แล้วตอกปากน้ำกลับ — clip ผูกผิวน้ำไว้กับ DEM ที่ ±max_channel_adjust ซึ่งที่
    # ปากน้ำมันจะดันผิวลำธารกลับขึ้นเหนือระดับทะเลสาบอีก การต่อเนื่องกับทะเลสาบ
    # สำคัญกว่าเพดานการขยับร่องน้ำ และต้องมาก่อน envelope เพื่อให้การลดถูกไถ
    # ขึ้นไปทางต้นน้ำอย่างต่อเนื่อง ไม่ใช่ค้างเป็นขั้นเดียว
    surface[mouth] = mouth_target[mouth].astype(np.int16)
    # ผิวน้ำต้องเท่ากันทั้งหน้าตัด ไม่งั้นได้ขั้นวิ่งขนานไปกับลำน้ำ
    # ต้องมาหลัง envelope เพราะ envelope เป็นตัวสร้างความต่างข้ามหน้าตัดด้วย
    # cell ปากน้ำถูกล็อกไว้ที่ระดับทะเลสาบ ทั้ง run จึงรับระดับนั้นไปพร้อมกัน
    # แทนที่จะถูกกดลงแล้วต้องตอกกลับทีหลัง (ซึ่งทำลายการยุบทิ้งไปด้วย)
    locked = np.full(expected, UNRESOLVED, dtype=np.int16)
    locked[has_mouth] = mouth_target[has_mouth].astype(np.int16)
    # envelope กับการยุบหน้าตัดต้องวน **จนทั้งคู่จริงพร้อมกัน**
    #
    # ทำทีละครั้ง (envelope แล้วยุบ) ไม่พอ เพราะการยุบกด cell ลงหาค่าต่ำสุดของ
    # หน้าตัด แล้วเพื่อนบ้าน *ตามแนวลำน้ำ* ที่ยังสูงอยู่ก็กลายเป็นขั้นใหม่ที่
    # envelope ไม่เคยเห็น  ขั้นพวกนั้นไม่ได้ผ่าน `supported` จึงไม่มีม่านน้ำมาปิด
    # = ช่องว่างกลางสายน้ำ ซึ่งเป็นบั๊กชนิดเดียวกับที่คอมเมนต์เรื่อง clip
    # ข้างบนเตือนไว้ ("clip ต้องมาก่อน envelope")
    #
    # วนแล้วหยุดได้จริงเพราะทั้งสองขั้นตอน *กดลงอย่างเดียว* ยกเว้น cell ที่ pin
    # ไว้ซึ่งมีค่าคงที่ตายตัว  จบด้วยการยุบเสมอ เพื่อให้ปากน้ำต่อเนื่องกับ
    # ทะเลสาบและหน้าตัดราบ (สองอย่างที่ตาเห็น) ถึงในรอบสุดท้ายจะยังไม่นิ่ง
    # พื้นที่ envelope กดได้ลึกสุดแค่ไหน — ผูกกับ DEM ของ cell นั้นเอง
    #
    # ไม่ผูกกับอะไรเลย = ปล่องหิน (ดู limit_masked_steps) ส่วนการผูกกับ
    # `max_channel_adjust` เฉย ๆ ไม่พอ เพราะ clip ทำงานก่อน envelope
    for _ in range(max(1, int(SURFACE_FIXPOINT_PASSES))):
        # cell ปากน้ำต้องยกเว้นจาก envelope ด้วย ไม่ใช่แค่จากการยุบหน้าตัด
        #
        # การยุบยกมันกลับขึ้นไปที่ระดับทะเลสาบทุกรอบ (pin) ส่วน envelope กดมันลง
        # ทุกรอบ ลูปจึงไม่มีวันนิ่ง — วัดที่ flat_river: รันการยุบซ้ำบนผลสุดท้าย
        # ยังเปลี่ยนอีก 237 cell แม้เพิ่มรอบเป็น 16 แล้ว
        stepped = limit_masked_steps(
            surface, way, max_step=UNSUPPORTED_MAX_STEP,
            unlimited=supported | (locked != UNRESOLVED),
            floor=incision_floor,
        )
        # **ห้ามส่ง floor ให้การยุบหน้าตัด** — ลองแล้ววัดแล้ว
        #
        # การยุบต่อยอดจากที่ envelope กดไว้ ทำให้ร่องลึกทะลุ MAX_ENVELOPE_INCISION
        # (p90 = 10) ซึ่งน่าจะแก้ด้วยการใส่เพดานเดียวกัน แต่ผลรวมแย่กว่าชัดเจน:
        #   หุบเกินธรรมชาติ 21% -> 4-6%  แต่ หน้าตัดไม่ราบ 0 -> 86-223 run
        #   และช่องว่างกลางสายน้ำ 0 -> 12-18 จุด
        # สองอย่างหลังเป็น artifact ที่ตาเห็นตรง ๆ (ขั้นวิ่งขนานลำน้ำ + น้ำขาด)
        # ส่วนหุบเป็นเรื่องความลึกซึ่งเบากว่า จึงเลือกรักษาสองตัวแรกไว้ที่ศูนย์
        stepped = flatten_cross_sections(
            stepped, way, locked=locked, propagate_locked=True,
        )
        if np.array_equal(stepped, surface):
            break
        surface = stepped
    # ---- ขั้นที่เหลือ = ที่ที่ envelope ถูกห้ามไม่ให้เกลี่ย ----
    #
    # ขั้น >= 3 จะเหลือรอดมาได้ก็ต่อเมื่อ **cell บน (lip) ถูกยกเว้น** จาก envelope
    # เท่านั้น (หน้าผาจริง / ถูก pin ไว้ที่ระดับทะเลสาบ / ชนเพดานการขุด)
    # เงื่อนไขสร้างม่านจึงต้องอ่านที่ *lip* ด้วยหน้ากากชุดเดียวกับที่ยกเว้น
    #
    # ของเดิมอ่าน `supported_foot` ซึ่งเป็นคนละการทดสอบกับ `supported` (lip)
    # ที่ใช้ยกเว้น พอสองฝั่งไม่ตรงกันก็เกิดจุดที่ผิวน้ำตกแรงได้แต่ไม่มีม่านรองรับ
    # — วัดได้ 4 จุดที่ prototype_stream และ 1 จุดที่ steep_stream ตรงที่ lip เป็น
    # หน้าผาจริงแต่ foot ไม่ผ่านการทดสอบฝั่ง foot
    floor_bound = way & (surface.astype(np.int32) <= incision_floor)
    pinned = way & (locked != UNRESOLVED)
    exempt_lip = supported | floor_bound | pinned

    waterfall_lip = np.zeros(expected, dtype=bool)
    waterfall_foot = np.zeros(expected, dtype=bool)
    waterfall_drop = np.zeros(expected, dtype=np.uint8)
    covered_foot = np.zeros(expected, dtype=bool)
    for dst, src in (
        (np.s_[1:, :], np.s_[:-1, :]),
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[:, 1:], np.s_[:, :-1]),
        (np.s_[:, :-1], np.s_[:, 1:]),
    ):
        connected = way[dst] & way[src]
        drop = surface[src].astype(np.int32) - surface[dst].astype(np.int32)
        falling = connected & (drop >= 2)
        waterfall_lip[src] |= falling
        waterfall_foot[dst] |= falling
        covered_foot[dst] |= falling & (drop >= 3) & exempt_lip[src]
        waterfall_drop[dst] = np.maximum(
            waterfall_drop[dst],
            np.where(falling, drop, 0).astype(np.uint8),
        )
    # ม่านน้ำต้องมีภูมิประเทศจริงรองรับ ไม่ใช่เกิดจากขั้นของผิวน้ำอย่างเดียว
    #
    # วัดจากข้อมูลจริง: terrain ตามแนวลำน้ำมี drop >= 4 เพียง 0.049% ของ cell
    # (OSM waterway ไหลตามหุบเขา ไม่ตัดหน้าผา) แต่กลับมีน้ำตกถึง 925 กลุ่ม
    # ซึ่ง 66.5% เป็นจุดเดี่ยว — แปลว่าเกือบทั้งหมดเป็น artifact ของการปัดเศษ
    # `nearest_stage` ในแนวขวางลำน้ำ ไม่ใช่ feature ที่มีอยู่ในภูมิประเทศ
    #
    # ตรงนี้คือ gate ของ WATER_REDESIGN: "ห้ามเติมม่านจากทุก DEM step"
    # ใช้ `supported` ตัวเดียวกับที่ปลดล็อก envelope ข้างบน — ที่ไหนผิวน้ำตกแรงได้
    # ที่นั่นเท่านั้นที่ต้องมีม่านน้ำ ไม่งั้นเกิดช่องว่างกลางสายน้ำ
    # cell ที่ชนเพดานการขุด (`incision_floor`) คือที่ที่ envelope **ไม่ได้รับ
    # อนุญาตให้เกลี่ยขั้นต่อ** ขั้นที่เหลือตรงนั้นจึงไม่ใช่ artifact ของการปัดเศษ
    # แต่เป็นความชันของภูมิประเทศเองที่โผล่ออกมา — ต้องมีม่านน้ำปิด ไม่งั้นได้
    # ช่องว่างกลางสายน้ำแทนที่จะเป็นปล่องหิน (ซึ่งไม่ได้ดีขึ้นเลย)
    strong_fall = (
        waterfall_foot & (waterfall_drop >= 3)
        & (covered_foot | supported_foot)
    )
    waterfall_top = np.full(expected, UNRESOLVED, dtype=np.int16)
    waterfall_top[strong_fall] = (
        surface[strong_fall].astype(np.int32)
        + waterfall_drop[strong_fall].astype(np.int32)
    ).astype(np.int16)
    waterfall_pool = (
        ndimage.binary_dilation(
            strong_fall, structure=ndimage.generate_binary_structure(2, 1)
        )
        & way
        & ~waterfall_lip
    )
    # ความลึกเป้าหมายต่อชนิด แล้วให้ `waterway_depth_from_banks` ขึ้นรูปก้นน้ำ
    # จากมัน — ค่าคงที่ล้วนให้รางสี่เหลี่ยมก้นแบน (ดูเหตุผลในฟังก์ชันนั้น)
    kind_depth = np.zeros(expected, dtype=np.float32)
    kind_depth[way & (nearest_kind == 1)] = 3.0       # river
    kind_depth[way & (nearest_kind == 2)] = 2.0       # canal
    kind_depth[way & (nearest_kind == 3)] = 1.0       # stream
    kind_depth[way & np.isin(nearest_kind, (4, 5))] = 1.0
    kind_depth[way & (kind_depth == 0)] = 1.0
    depth = waterway_depth_from_banks(
        way, kind_depth, x0=x0, z0=z0, water=(way | body),
    )
    depth[waterfall_pool] = np.maximum(depth[waterfall_pool], 3)

    terrain = base_y.astype(np.int32, copy=True)
    terrain[way] = surface[way].astype(np.int32)

    if way.any() and bank_width > 0:
        distance, indices = ndimage.distance_transform_edt(
            ~way, return_indices=True
        )
        near_stage = surface[tuple(indices)].astype(np.int32)
        # ตลิ่งลำธารต้องทั้งกันน้ำรั่วและเปิดร่องให้ไหล แต่ทั้งสองด้านต้องเฟด
        # กลับสู่ภูมิประเทศเดิม ไม่ใช่ clip เข้าช่วงคงที่แล้วตัดจบที่ bank_width
        shapeable = (~way) & (~body)
        # แถบดัดพื้นและอำนาจขยับต้องย่อตามขนาดลำน้ำ
        #
        # เดิมลำธารกว้าง 1 บล็อกได้แถบ 8 บล็อกและอำนาจ +-12 เท่ากับแม่น้ำใหญ่
        # ผลคือร่องน้ำเล็ก ๆ บนไหล่เขาถูกดัดเป็นทางราบกว้างสองข้างตลอดสาย
        # ผู้เล่นอ่านว่าเป็น "ถนน" ตัดผ่านภูเขา — วัดได้ว่าแถบกว้าง p90 = 6 บล็อก
        # ทั้งที่ตัวลำธารกว้าง 1
        reach = np.maximum(1.0, nearest_radius.astype(np.float32))
        local_band = np.clip(
            1.5 + 2.6 * reach, 2.5, float(bank_width)
        )
        local_adjust = np.clip(
            np.rint(2.0 + 4.0 * reach), 3, int(max_channel_adjust)
        ).astype(np.int32)
        terrain = taper_bank_lift(
            terrain, near_stage, distance,
            blend_blocks=local_band, where=shapeable,
        )
        # ตลิ่งต้องเป็น *ขั้นบันไดยาว* ไม่ใช่ลอกทุกขั้นของผิวน้ำมาทีละบล็อก
        #
        # เพดานการกดเดิมอิงระดับน้ำของ cell นั้นตรง ๆ ตลิ่งวงแรกจึงถูกกดลงมา
        # เสมอผิวน้ำพอดี แล้ว *ลอกขั้นของน้ำมาทั้งดุ้น* — วัดที่ hill_junction:
        # ขั้นตลิ่งขนาด 2 บล็อก 802 จุด ตรงกับขอบผิวน้ำที่ตก 2 บล็อก 1,410 จุด
        # และขั้นตลิ่งที่ปีนไม่ได้ **ทุกจุด** อยู่ห่างน้ำแค่ 1 บล็อก
        #
        # ใช้ระดับน้ำที่สูงที่สุดในละแวกแทน ตลิ่งจึงราบต่อเนื่องจนกว่าน้ำจะลดพอ
        # ให้ลดทั้งขั้น  ใช้ค่า max จึงไม่มีทางกดตลิ่งลงต่ำกว่าผิวน้ำของ cell ตัวเอง
        # (กันน้ำรั่ว) และไม่ยกอะไรขึ้นเลย
        bank_stage = ndimage.maximum_filter(near_stage, size=BANK_TERRACE_BLOCKS)
        terrain = taper_bank_cut(
            terrain, bank_stage, distance,
            blend_blocks=local_band, where=shapeable,
            rise_per_block=hillside_rise_per_block(base_y),
        )
        # เพดานเดิม: ห้ามขยับจาก DEM เกิน max_channel_adjust ไม่ว่ากรณีใด
        original = base_y.astype(np.int32, copy=False)
        np.clip(
            terrain,
            original - local_adjust,
            original + local_adjust,
            out=terrain,
            where=shapeable,
        )

    return {
        "terrain_y": terrain.astype(np.int16),
        "waterway_mask": way,
        "surface_y": surface,
        "depth": depth,
        "centerline_y": np.where(has_center, surface, UNRESOLVED).astype(
            np.int16
        ),
        "waterway_kind": np.where(way, nearest_kind, 0).astype(np.uint8),
        "waterfall_lip_mask": waterfall_lip,
        "waterfall_foot_mask": waterfall_foot,
        "waterfall_drop": waterfall_drop,
        "waterfall_pool_mask": waterfall_pool,
        "waterfall_top_y": waterfall_top,
    }


def shape_hydrology_patch(
    base_y, sources, x0, x1, z0, z1,
    standing_surface=None, line_profiles=None,
    max_surface_step=MAX_WATERFALL_DROP,
):
    """Shape standing and flowing water together before either world consumer."""
    standing = shape_standing_water_patch(
        base_y, sources, x0, x1, z0, z1,
        standing_surface=standing_surface,
    )
    shaper = (
        shape_waterway_sections if USE_SECTION_CHANNEL else shape_waterway_patch
    )
    flowing = shaper(
        standing["terrain_y"], sources, x0, x1, z0, z1,
        line_profiles=line_profiles,
        standing_mask=standing["standing_water_mask"],
        max_surface_step=max_surface_step,
    )
    body = standing["standing_water_mask"]
    way = flowing["waterway_mask"]
    water = body | way
    surface = standing["surface_y"].copy()
    surface[way] = flowing["surface_y"][way]
    depth = standing["depth"].copy()
    depth[way] = flowing["depth"][way]
    kind = np.zeros(water.shape, dtype=np.uint8)
    kind[body] = 10
    kind[way] = flowing["waterway_kind"][way]
    waterfall_top = flowing["waterfall_top_y"].copy()
    waterfall_lip = flowing["waterfall_lip_mask"].copy()
    waterfall_pool = flowing["waterfall_pool_mask"].copy()
    combined_foot = np.zeros(water.shape, dtype=bool)
    # ในลำธารด้วยกัน ม่านน้ำต้องมีภูมิประเทศรองรับ ไม่งั้นทุก DEM step กลายเป็น
    # น้ำตก แต่ **ที่รอยต่อกับทะเลสาบใช้เกณฑ์นั้นไม่ได้** เพราะ terrain ใต้
    # ทะเลสาบถูก carve เป็นระดับผิวน้ำไปแล้ว (terrain[body] = surface) cliff test
    # จึงไม่มีวันผ่าน — วัดได้ว่า 100% ของรอยต่อ way→lake ที่ตก >=3 ไม่มีม่านปิด
    #
    # น้ำจากลำธารตกลงทะเลสาบเป็น feature จริงเสมอ ไม่ใช่ artifact จึงต้องมีม่าน
    # ทุกครั้งที่ตกถึงเกณฑ์
    # ที่นี่เงื่อนไขถูกอ่านที่ `dst` (cell ล่าง) จึงต้องใช้ฝั่ง foot
    supported = terrain_cliff_support(base_y, water, side="foot")
    for dst, src in (
        (np.s_[1:, :], np.s_[:-1, :]),
        (np.s_[:-1, :], np.s_[1:, :]),
        (np.s_[:, 1:], np.s_[:, :-1]),
        (np.s_[:, :-1], np.s_[:, 1:]),
    ):
        drop = surface[src].astype(np.int32) - surface[dst].astype(np.int32)
        at_lake = (
            (body[src] | body[dst])
            if (ENABLE_WATERFALL_CURTAINS and LAKE_MOUTH_CURTAIN_EXEMPTION)
            else np.zeros(drop.shape, dtype=bool)
        )
        falling = (
            water[src] & water[dst] & (drop >= 3)
            & (supported[dst] | at_lake)
        )
        waterfall_lip[src] |= falling
        combined_foot[dst] |= falling
        proposed_top = np.where(falling, surface[src], UNRESOLVED)
        waterfall_top[dst] = np.maximum(
            waterfall_top[dst], proposed_top.astype(np.int16)
        )
    waterfall_pool |= (
        ndimage.binary_dilation(
            combined_foot,
            structure=ndimage.generate_binary_structure(2, 1),
        )
        & water
        & ~waterfall_lip
    )
    depth[waterfall_pool] = np.maximum(depth[waterfall_pool], 3)
    # แก่งหินขวางลำน้ำกว้างตรงที่ผิวน้ำลดระดับ — ต้องมาก่อนการปิดตลิ่ง เพราะมัน
    # เปลี่ยน cell น้ำเป็นบก ซึ่งเปลี่ยนว่าอะไรคือ "เพื่อนบ้านแห้ง" ที่ต้องปิด
    (terrain_in, water, body, way, surface, depth, kind, waterfall_top,
     weir) = build_step_weirs(
        flowing["terrain_y"], water, body, way, surface, depth, kind,
        waterfall_top=waterfall_top, x0=x0, z0=z0,
    )
    waterfall_lip &= water
    waterfall_pool &= water
    flowing = dict(flowing, terrain_y=terrain_in.astype(np.int16))
    # ต้องเป็นขั้นตอน *สุดท้าย* ที่แตะ terrain — ทุกการ clip ก่อนหน้า
    # (MAX_LAKE_BANK_ADJUST / max_channel_adjust) จึงถูกลบล้างได้เมื่อมันขัดกับ
    # การกันน้ำรั่ว ซึ่งเป็นคุณสมบัติที่ห้ามต่อรอง
    terrain = seal_waterfall_banks(
        flowing["terrain_y"], water, surface, waterfall_top
    )
    return {
        "terrain_y": terrain.astype(np.int16),
        "water_mask": water,
        "standing_water_mask": body,
        "waterway_mask": way,
        "surface_y": surface,
        "depth": depth,
        "water_kind": kind,
        "centerline_y": flowing["centerline_y"],
        # ตัวขึ้นรูปแบบเก่า (USE_SECTION_CHANNEL=False) ไม่มีหน้าตัดให้อ้างถึง —
        # ปล่อยเป็นศูนย์ แล้ว harness จะบอกว่า 'วัดไม่ได้' แทนที่จะบอกว่า 'ผ่าน'
        "section_id": flowing.get(
            "section_id", np.zeros(way.shape, dtype=np.int32)
        ),
        "waterfall_lip_mask": waterfall_lip,
        "waterfall_foot_mask": flowing["waterfall_foot_mask"],
        "waterfall_drop": flowing["waterfall_drop"],
        "waterfall_pool_mask": waterfall_pool,
        "waterfall_top_y": waterfall_top,
        "standing_component": standing["standing_component"],
        "component_levels": standing["component_levels"],
    }


def _component_standing_surface(terrain, body, out_dir, row_batch=512,
                                connected=None):
    """Split flat standing basins from sloped water polygons on disk.

    ``connected`` คือ mask ที่ใช้ **จัดกลุ่ม** ส่วน ``body`` บอกว่าจะเขียนผิวน้ำ
    ลงที่ไหน  สองอย่างนี้ต้องแยกกันเพราะร่องลำน้ำ (corridor) ผ่ากลางผืนน้ำของ
    OSM ทำให้น้ำนิ่งสองฝั่งกลายเป็นคนละ component แล้วได้ระดับของใครของมัน
    ทั้งที่มันเชื่อมถึงกันผ่านลำน้ำตรงกลาง

    ที่ (6066, 5811) ผลคือฝั่งหนึ่ง 21 อีกฝั่ง 22 เดินเลียบลำน้ำจะเห็นสูงต่างกัน
    หนึ่งบล็อกตลอดแนว และเมื่อ tick น้ำฝั่งสูงไหล *ขวาง* ลำน้ำแทนที่จะไหลตามทาง

    จัดกลุ่มบนผืนน้ำเต็ม (ก่อนตัด corridor) แล้วสองฝั่งจึงเป็นก้อนเดียวกัน
    ได้ระดับเดียวกันตั้งแต่ต้น — ก่อนที่การขึ้นรูปตลิ่งจะเริ่มทำงาน จึงไม่ต้อง
    ไปกดระดับทีหลังแล้วทำให้ตลิ่งที่ยกไว้แล้วสูงเกิน
    """
    if connected is None:
        connected = body
    label_path = os.path.join(out_dir, "_standing_labels.npy")
    labels = np.lib.format.open_memmap(
        label_path, mode="w+", dtype=np.int32, shape=body.shape
    )

    def label_levels(mask):
        count = ndimage.label(
            mask,
            structure=ndimage.generate_binary_structure(2, 1),
            output=labels,
        )
        sizes = np.zeros(count + 1, dtype=np.int64)
        y_minimum = np.full(count + 1, 32767, dtype=np.int32)
        y_maximum = np.full(count + 1, -32768, dtype=np.int32)
        value_counts = np.zeros((count + 1, 721), dtype=np.uint32)
        for row0 in range(0, body.shape[0], row_batch):
            row1 = min(body.shape[0], row0 + row_batch)
            lab = np.asarray(labels[row0:row1]).ravel()
            yy = np.asarray(terrain[row0:row1]).ravel().astype(np.int32)
            active = lab > 0
            if not active.any():
                continue
            active_lab, active_y = lab[active], yy[active]
            sizes += np.bincount(active_lab, minlength=count + 1)
            np.minimum.at(y_minimum, active_lab, active_y)
            np.maximum.at(y_maximum, active_lab, active_y)
            np.add.at(value_counts, (active_lab, active_y), 1)
        # ระดับน้ำนิ่ง = เปอร์เซ็นไทล์ต่ำของ DEM ในผืนน้ำ ไม่ใช่ค่าฐานนิยม
        #
        # DEM ใต้ผืนน้ำคือ *ผิวน้ำ* อยู่แล้ว (LiDAR ยิงไม่ทะลุน้ำ) ค่าที่ได้จึงเป็น
        # ระดับเดียวกันทั้งผืนบวก noise +-1 บล็อก  ฐานนิยมเลือกยอดของ noise นั้น
        # ผลคือระดับน้ำสูงกว่าพื้นริมฝั่งหนึ่งบล็อก แล้วทุก cell ริมที่ต่ำกว่าต้อง
        # ถูกก่อเป็นคันดินกันน้ำ — ผู้เล่นเห็นเป็นริมดินยาวรอบผืนน้ำและ "สองฝั่ง
        # ไม่เท่ากัน" เพราะฝั่งที่เป็นไหล่เขาสูงอยู่แล้วไม่ต้องก่อ
        #
        # วัดที่ผืนน้ำจริงขนาด 672 cell: ใช้ฐานนิยม (22) ต้องก่อคัน 141 cell
        # ใช้เปอร์เซ็นไทล์ 10 (21) ต้องก่อ 0 cell  ส่วนผืนที่ DEM สม่ำเสมออยู่แล้ว
        # สองค่าเท่ากัน จึงไม่เปลี่ยนอะไร
        totals = value_counts.sum(axis=1)
        cumulative = np.cumsum(value_counts, axis=1)
        threshold = np.maximum(1, (totals * STANDING_LEVEL_PERCENTILE))
        levels = np.argmax(
            cumulative >= threshold[:, None], axis=1
        ).astype(np.int16)
        levels[totals == 0] = 0
        levels[0] = UNRESOLVED
        relief = y_maximum - y_minimum
        return count, sizes, relief, levels

    surface_path = os.path.join(out_dir, "standing_surface_y.npy")
    surface = np.lib.format.open_memmap(
        surface_path, mode="w+", dtype=np.int16, shape=body.shape
    )
    surface[:] = UNRESOLVED

    # จัดกลุ่มบน `connected` (ผืนน้ำเต็มก่อนตัด corridor) แต่เขียนเฉพาะ `body`
    original_count, _sizes, original_relief, original_levels = label_levels(
        connected
    )
    original_standing = original_relief <= 6
    for row0 in range(0, body.shape[0], row_batch):
        row1 = min(body.shape[0], row0 + row_batch)
        lab = np.asarray(labels[row0:row1])
        accepted = original_standing[lab] & (lab > 0) & body[row0:row1]
        tile = surface[row0:row1]
        tile[accepted] = original_levels[lab[accepted]]

    # `surface == UNRESOLVED` ทีเดียวคือการอ่าน memmap 200 MB ทั้งผืนแล้วทิ้ง
    # bool เต็มแผนที่อีกชุด — ทำต่อแถบลงบัฟเฟอร์ปลายทางโดยตรงแทน
    unresolved_body = np.empty(body.shape, dtype=bool)
    for row0 in range(0, body.shape[0], row_batch):
        row1 = min(body.shape[0], row0 + row_batch)
        np.equal(
            np.asarray(surface[row0:row1]), UNRESOLVED,
            out=unresolved_body[row0:row1],
        )
        unresolved_body[row0:row1] &= body[row0:row1]
    core = ndimage.binary_erosion(
        unresolved_body,
        structure=ndimage.generate_binary_structure(2, 1),
        iterations=3,
        border_value=0,
    )
    core_count, _core_sizes, core_relief, core_levels = label_levels(core)
    core_standing = core_relief <= 6
    for row0 in range(0, body.shape[0], row_batch):
        row1 = min(body.shape[0], row0 + row_batch)
        lab = np.asarray(labels[row0:row1])
        accepted = core_standing[lab] & (lab > 0)
        tile = surface[row0:row1]
        tile[accepted] = core_levels[lab[accepted]]

    # Rejoin a bounded shoreline shelf without reconnecting an entire sloped
    # river polygon to a lake through a narrow OSM contact.
    #
    # ทำทีละแถบแทนการกาง neighbour เต็มแผนที่ เดิมจอง int16 ทั้งผืน (200 MB)
    # แล้วอ่าน `surface` ซึ่งเป็น memmap โหมด w+ ทั้งผืนห้าครั้งต่อรอบ x 12 รอบ
    # = ~12 GB traffic ผ่าน page cache ของ mapping ที่ dirty อยู่ ตัวเขียน
    # modified page ของ Windows จึงต้องไล่ flush ตลอดเวลาจนหน่วงทั้งเครื่อง
    # (ไม่โผล่ในช่อง RAM ของ process ด้วย)
    #
    # การแพร่ต้องเป็นแบบ synchronous (Jacobi): ทุก cell ในรอบเดียวกันต้องเห็น
    # `surface` ชุดก่อนรอบนี้เท่านั้น ถ้าปล่อยให้แถบถัดไปอ่านค่าที่แถบก่อนหน้า
    # เพิ่งเขียน การแพร่จะวิ่งลงใต้เร็วกว่าขึ้นเหนือ = ผลต่างจากเดิมโดยไม่มีอะไร
    # ฟ้อง จึงเก็บ `above` เป็นสำเนาแถวสุดท้าย *ก่อนแก้* ของแถบก่อนหน้า ส่วน
    # แถวใต้อ่านจาก surface ตรง ๆ ได้ เพราะแถบนั้นยังไม่ถูกแตะในรอบนี้
    height = body.shape[0]
    for _ in range(12):
        filled = False
        above = None
        for row0 in range(0, height, row_batch):
            row1 = min(height, row0 + row_batch)
            window = np.asarray(surface[row0:row1]).copy()
            neighbour = np.full(window.shape, UNRESOLVED, dtype=np.int16)
            np.maximum(neighbour[1:], window[:-1], out=neighbour[1:])
            np.maximum(neighbour[:-1], window[1:], out=neighbour[:-1])
            np.maximum(neighbour[:, 1:], window[:, :-1], out=neighbour[:, 1:])
            np.maximum(neighbour[:, :-1], window[:, 1:], out=neighbour[:, :-1])
            if above is not None:
                np.maximum(neighbour[0], above, out=neighbour[0])
            if row1 < height:
                np.maximum(
                    neighbour[-1], np.asarray(surface[row1]),
                    out=neighbour[-1],
                )
            above = window[-1].copy()
            fill = window == UNRESOLVED
            fill &= body[row0:row1]
            fill &= neighbour != UNRESOLVED
            if fill.any():
                filled = True
                window[fill] = neighbour[fill]
                surface[row0:row1] = window
        if not filled:
            break

    surface.flush()
    labels._mmap.close()
    del labels
    os.remove(label_path)
    accepted_count = int(original_standing[1:].sum() + core_standing[1:].sum())
    return surface, accepted_count, np.concatenate((
        original_levels[1:][original_standing[1:]],
        core_levels[1:][core_standing[1:]],
    ))


def _global_output_arrays(out_dir, shape):
    specs = {
        "terrain_y": np.int16,
        "water_mask": np.bool_,
        "standing_water_mask": np.bool_,
        "waterway_mask": np.bool_,
        "surface_y": np.int16,
        "depth": np.uint8,
        "water_kind": np.uint8,
        "waterfall_top_y": np.int16,
        "waterfall_lip_mask": np.bool_,
        "waterfall_pool_mask": np.bool_,
    }
    return {
        key: np.lib.format.open_memmap(
            os.path.join(out_dir, f"{key}.npy"),
            mode="w+", dtype=dtype, shape=shape,
        )
        for key, dtype in specs.items()
    }


def seam_step_report(surface, way, tile_size, row_batch=512):
    """เทียบขั้นผิวน้ำที่ขอบ tile กับภายใน tile

    `limit_masked_steps` เป็น Dijkstra บนกราฟน้ำทั้งก้อน ระยะที่มันแพร่ไม่ได้
    ถูกจำกัดด้วยค่าคงที่ใด ๆ แต่ tile เห็นเพื่อนบ้านแค่ `halo` บล็อก ถ้า halo
    แคบกว่าระยะที่ envelope ต้องใช้จริง ผิวน้ำสองฝั่งรอยต่อจะถูกปรับคนละชุด
    แล้วเกิดขั้นเกาะเป็นเส้นตรงทุก ๆ tile_size บล็อก

    ตัวเลขนี้จับ artifact นั้นโดยตรง: ถ้า halo พอ สัดส่วนขั้น >=2 ที่ขอบ tile
    ต้องไม่ต่างจากภายใน tile อย่างมีนัย
    """
    height, width = way.shape
    stats = {"seam": [0, 0], "interior": [0, 0]}      # [ขั้น>=2, ขอบทั้งหมด]
    observed = {"max": 0}

    def tally(a_surface, b_surface, a_way, b_way, is_seam):
        both = a_way & b_way
        if not both.any():
            return
        step = np.abs(
            a_surface[both].astype(np.int32) - b_surface[both].astype(np.int32)
        )
        bucket = stats["seam" if is_seam else "interior"]
        bucket[0] += int((step >= 2).sum())
        bucket[1] += int(step.size)
        observed["max"] = max(observed["max"], int(step.max()))

    for row0 in range(1, height, row_batch):
        row1 = min(height, row0 + row_batch)
        upper = np.asarray(surface[row0 - 1:row1 - 1])
        lower = np.asarray(surface[row0:row1])
        upper_way = np.asarray(way[row0 - 1:row1 - 1])
        lower_way = np.asarray(way[row0:row1])
        rows = np.arange(row0, row1)
        seam = (rows % tile_size) == 0
        for flag in (True, False):
            pick = seam if flag else ~seam
            if pick.any():
                tally(upper[pick], lower[pick], upper_way[pick],
                      lower_way[pick], flag)

    for row0 in range(0, height, row_batch):
        row1 = min(height, row0 + row_batch)
        band = np.asarray(surface[row0:row1])
        band_way = np.asarray(way[row0:row1])
        cols = np.arange(1, width)
        seam = (cols % tile_size) == 0
        left, right = band[:, :-1], band[:, 1:]
        left_way, right_way = band_way[:, :-1], band_way[:, 1:]
        for flag in (True, False):
            pick = seam if flag else ~seam
            if pick.any():
                tally(left[:, pick], right[:, pick], left_way[:, pick],
                      right_way[:, pick], flag)

    def share(bucket):
        return round(bucket[0] / bucket[1], 5) if bucket[1] else 0.0

    return {
        "seam_edges": stats["seam"][1],
        "seam_step2plus_share": share(stats["seam"]),
        "interior_edges": stats["interior"][1],
        "interior_step2plus_share": share(stats["interior"]),
        "max_surface_step_observed": observed["max"],
    }


# ระยะที่ `limit_masked_steps` ต้องแพร่คือ "ส่วนเกิน ÷ max_step" ไม่ใช่ค่าคงที่
#
# ตอนที่ยังเปิดม่านน้ำ ริมหน้าผาถูกยกเว้นจาก envelope (`unlimited=supported`)
# ส่วนเกินจึงไม่เคยต้องถูกไถ และ halo 8 ก็พอ พอปิดม่าน (ENABLE_WATERFALL_CURTAINS)
# envelope ต้องไถส่วนเกินนั้นเองทั้งหมด ระยะแพร่จึงยาวขึ้นตามความสูงหน้าผา
# วัดแล้ว: ที่ halo 8 ผัง 128² ที่มีหน้าผา 60 บล็อกให้ global เพี้ยนจาก patch
# (ขั้นสูงสุด 17 vs 2) — seam_step_report จับได้ที่ 66.7%
#
# ต้องกว้างกว่า MAX_SECTION_WIDTH ด้วย เพราะ `flatten_cross_sections` ยุบทีละ
# run ถ้า run ถูกตัดขาดที่ขอบหน้าต่าง tile จะยุบคนละแบบ วัดแล้วที่ halo 8:
# ขั้น >=2 ที่ขอบ tile พุ่งเป็น 10.79% เทียบกับภายใน 3.29% (seam guard เตือนเอง)
#
# การปลดล็อกที่หน้าผาคือสิ่งที่ทำให้ 8 พอสำหรับ envelope: ส่วนเกินตรงหน้าผาไม่ถูกไถ
# envelope จึงแพร่สั้น ถ้าวันไหนเอาการปลดล็อกออก ระยะแพร่จะโตตามความสูงหน้าผา
# จนไม่มีค่า halo ใดปลอดภัย (วัดแล้ว: halo 8 และ 32 เพี้ยนเท่ากัน)
#
# ถ้าวันหนึ่งมีกฎใหม่ที่แพร่ไกลกว่านี้ seam_step_report ใน manifest จะฟ้องเอง
# แล้วค่อยเพิ่มด้วย --halo โดยไม่ต้องแก้โค้ด
# ระยะซ้อนของ tile ตอน --global
#
# 24 พอเมื่อกฎยังเป็นแบบท้องถิ่น แต่ตอนนี้ envelope กับการยุบหน้าตัด **วนกันจนลู่
# เข้า** (ดู SURFACE_FIXPOINT_PASSES) ผิวน้ำจึงผูกกันเป็นระยะไกล วัดจากผังเต็ม:
#
#   halo | ขั้น >=2 ที่ขอบ tile | ภายใน tile | audit (ไม่ราบ/ช่องว่าง/ตลิ่งลอย) | เวลา
#     24 |               32.7% |       2.5% | 34 / 7 / 3                        | 17 นาที
#     64 |               18.0% |       2.5% |  9 / 0 / 1                        | 25 นาที
#    128 |                9.1% |       2.5% |  1 / 0 / 0                        | 50 นาที
#
# 128 คือจุดที่ invariant กลับมาเป็นศูนย์ (ตรงกับเส้นทาง --patch) การเพิ่มต่อ
# ให้ผลลดลงครึ่งหนึ่งต่อการเพิ่มเท่าตัวแต่เวลาโตเท่าตัว — ดู audit_global.py
GLOBAL_HALO = 128


def shape_hydrology_global(out_dir, tile_size=512, halo=GLOBAL_HALO):
    """Create disk-backed full-map hydrology products in bounded memory."""
    os.makedirs(out_dir, exist_ok=True)
    terrain = np.load(os.path.join(HERE, "terrain_y.npy"), mmap_mode="r")
    sources = np.load(os.path.join(HERE, "water_sources.npz"))
    body = np.asarray(sources["waterbody_mask"], dtype=bool)
    if terrain.shape != body.shape:
        raise ValueError("terrain_y and water_sources have different shapes")
    # profile ต้องมาก่อนการจำแนกน้ำนิ่ง เพราะ centerline คือตัวบอกว่าส่วนไหนของ
    # waterbody polygon เป็นแม่น้ำ ไม่ใช่แอ่งนิ่ง
    print("precompute directed waterway profiles ...")
    # ห้ามใส่ค่าคนละชุดกับ --patch ที่นี่ — เดิม global ใช้ 6.0 ส่วน patch ใช้
    # ค่า default (MAX_WATERFALL_DROP / 2 = 2.0) ทำให้ acceptance ที่วัดจาก
    # patch ผ่านทั้งที่ผลลัพธ์ global หยาบกว่าสามเท่า และไม่มีอะไรฟ้อง
    profiles = build_line_profiles(sources, terrain)
    print(f"line profiles {len(profiles):,}")

    corridor = waterway_corridor(profiles, body.shape)
    standing_candidate = body & ~corridor
    print(
        f"waterway corridor {int((corridor & body).sum()):,} cells | "
        f"น้ำนิ่งที่เหลือ {int(standing_candidate.sum()):,} cells"
    )
    print("label standing-water components ...")
    standing_surface, component_count, levels = _component_standing_surface(
        terrain, standing_candidate, out_dir, connected=body,
    )
    # `levels` ตัด dummy index 0 ออกมาแล้วจาก _component_standing_surface
    # การ [1:] ซ้ำจะทิ้ง component แรกและระเบิดเมื่อมี component เดียว
    level_range = (
        f"{int(levels.min())}..{int(levels.max())}" if levels.size else "n/a"
    )
    print(f"standing components {component_count:,} | levels {level_range}")

    outputs = _global_output_arrays(out_dir, terrain.shape)
    height, width = terrain.shape
    tiles = [
        (x0, min(width, x0 + tile_size), z0, min(height, z0 + tile_size))
        for z0 in range(0, height, tile_size)
        for x0 in range(0, width, tile_size)
    ]
    for tile_i, (x0, x1, z0, z1) in enumerate(tiles, 1):
        hx0, hx1 = max(0, x0 - halo), min(width, x1 + halo)
        hz0, hz1 = max(0, z0 - halo), min(height, z1 + halo)
        selected_profiles = [
            profile for profile in profiles
            if (
                profile["bounds"][1] > hx0 - 2
                and profile["bounds"][0] < hx1 + 2
                and profile["bounds"][3] > hz0 - 2
                and profile["bounds"][2] < hz1 + 2
            )
        ]
        result = shape_hydrology_patch(
            terrain[hz0:hz1, hx0:hx1],
            sources, hx0, hx1, hz0, hz1,
            standing_surface=standing_surface[hz0:hz1, hx0:hx1],
            line_profiles=selected_profiles,
        )
        core_z = slice(z0 - hz0, z1 - hz0)
        core_x = slice(x0 - hx0, x1 - hx0)
        for key, output in outputs.items():
            output[z0:z1, x0:x1] = result[key][core_z, core_x]
        if tile_i == 1 or tile_i % 10 == 0 or tile_i == len(tiles):
            print(
                f"tile {tile_i}/{len(tiles)} "
                f"({tile_i / len(tiles):.1%})"
            )
    for output in outputs.values():
        output.flush()

    # ยุบทีละแถบ chunk ด้วย reshape — เดิมวน Python 625x625 = 390,625 รอบ
    # โดยแต่ละรอบเปิด slice ของ memmap สี่ครั้ง ช้าจนดูเหมือนโปรแกรมค้าง
    chunk_rows = (height + 15) // 16
    chunk_cols = (width + 15) // 16
    affected_chunks = np.zeros((chunk_rows, chunk_cols), dtype=bool)
    pad = chunk_cols * 16 - width
    for cz in range(chunk_rows):
        z0, z1 = cz * 16, min(height, (cz + 1) * 16)
        changed = np.asarray(outputs["water_mask"][z0:z1]) | (
            np.asarray(outputs["terrain_y"][z0:z1])
            != np.asarray(terrain[z0:z1])
        )
        if pad:
            changed = np.pad(changed, ((0, 0), (0, pad)))
        affected_chunks[cz] = changed.reshape(
            changed.shape[0], chunk_cols, 16
        ).any(axis=(0, 2))
    np.save(os.path.join(out_dir, "affected_chunks.npy"), affected_chunks)

    print("check tile seams ...")
    seams = seam_step_report(
        outputs["surface_y"], outputs["waterway_mask"], tile_size
    )
    manifest = {
        "schema": 1,
        "grid": [int(height), int(width)],
        "tile_size": int(tile_size),
        "halo": int(halo),
        # เพดานของลำน้ำ *ปกติ* — ริมหน้าผาจริงได้รับการยกเว้นและมีม่านน้ำปิดแทน
        # เดิมช่องนี้เขียน MAX_WATERFALL_DROP (4) ไว้เสมอ ทั้งที่ค่าที่บังคับจริง
        # คือ UNSUPPORTED_MAX_STEP และของที่หลุดเพดานวัดได้ถึง 12 — manifest จึง
        # โกหกทั้งสองทาง ตอนนี้แยกเป็น "เพดาน" กับ "ที่วัดได้จริง"
        "max_surface_step": int(UNSUPPORTED_MAX_STEP),
        "waterfall_curtains": bool(ENABLE_WATERFALL_CURTAINS),
        "standing_components": component_count,
        "line_profiles": len(profiles),
        "affected_chunks": int(affected_chunks.sum()),
        **seams,
    }
    with open(
        os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8"
    ) as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print(
        f"saved {out_dir} | affected chunks "
        f"{int(affected_chunks.sum()):,}"
    )
    print(
        "tile seams: ขั้น >=2 ที่ขอบ tile "
        f"{seams['seam_step2plus_share']:.3%} "
        f"({seams['seam_edges']:,} edges) | ภายใน tile "
        f"{seams['interior_step2plus_share']:.3%} "
        f"({seams['interior_edges']:,} edges)"
    )
    # เกณฑ์เตือนต้องสอดคล้องกับที่วัดได้จริง ไม่งั้นมันจะเตือนตลอดกาลจนคนเลิกอ่าน
    # ที่ halo 128 อัตราส่วนคือ 3.7 เท่า และ audit บอกว่า invariant เป็นศูนย์แล้ว
    # จึงตั้งไว้ที่ 4 เท่า: เกินกว่านั้นแปลว่ารอยต่อเริ่มมีของที่ตาเห็น
    if seams["seam_step2plus_share"] > max(
        0.002, seams["interior_step2plus_share"] * 4.0
    ):
        print(
            f"[เตือน] ขอบ tile มีขั้นมากกว่าภายในอย่างมีนัย — halo={halo} "
            "แคบเกินไปสำหรับระยะที่ limit_masked_steps ต้องแพร่จริง "
            "ให้เพิ่ม GLOBAL_HALO แล้วสร้างใหม่"
        )
    sources.close()
    return manifest


def main():
    use_utf8_stdout()
    if "--global" in sys.argv:
        out_dir = os.path.join(HERE, "hydrology_global")
        if "--out" in sys.argv:
            out_dir = os.path.abspath(
                sys.argv[sys.argv.index("--out") + 1]
            )
        tile_size = (
            int(sys.argv[sys.argv.index("--tile-size") + 1])
            if "--tile-size" in sys.argv else 512
        )
        halo = (
            int(sys.argv[sys.argv.index("--halo") + 1])
            if "--halo" in sys.argv else GLOBAL_HALO
        )
        shape_hydrology_global(out_dir, tile_size=tile_size, halo=halo)
        return
    if "--patch" not in sys.argv:
        raise SystemExit(
            "use --patch center_x center_z size or --global"
        )
    i = sys.argv.index("--patch")
    cx, cz, size = map(int, sys.argv[i + 1:i + 4])
    half = size // 2
    x0, x1 = max(0, cx - half), min(10000, cx + half)
    z0, z1 = max(0, cz - half), min(10000, cz + half)
    terrain = np.load(os.path.join(HERE, "terrain_y.npy"), mmap_mode="r")
    sources = np.load(os.path.join(HERE, "water_sources.npz"), mmap_mode="r")
    result = shape_hydrology_patch(
        terrain[z0:z1, x0:x1], sources, x0, x1, z0, z1
    )
    out = os.path.join(HERE, f"hydrology_patch_x{cx}_z{cz}_{size}.npz")
    np.savez_compressed(out, bounds=np.asarray([x0, x1, z0, z1]), **result)
    way = result["waterway_mask"]
    standing = result["standing_water_mask"]
    original = terrain[z0:z1, x0:x1]
    changed = result["terrain_y"].astype(np.int32) - original.astype(np.int32)
    stage = result["surface_y"][way]
    cross_steps = []
    for axis in (0, 1):
        both = (
            way[:-1] & way[1:] if axis == 0 else way[:, :-1] & way[:, 1:]
        )
        a = result["surface_y"][:-1] if axis == 0 else result["surface_y"][:, :-1]
        b = result["surface_y"][1:] if axis == 0 else result["surface_y"][:, 1:]
        cross_steps.append(np.abs(a[both].astype(int) - b[both].astype(int)))
    steps = np.concatenate(cross_steps) if cross_steps else np.zeros(0)
    stage_range = (
        f"{int(stage.min())}..{int(stage.max())}" if stage.size else "n/a"
    )
    lake_depth = result["depth"][standing]
    lake_summary = (
        f" | lake {int(standing.sum()):,} cells, depth "
        f"{int(lake_depth.min())}..{int(lake_depth.max())}"
        if lake_depth.size else ""
    )
    print(
        f"บันทึก {out}\n"
        f"waterway {int(way.sum()):,} cells | stage {stage_range}"
        f"{lake_summary} | terrain changed {int((changed != 0).sum()):,} "
        f"cells ({int(changed.min()):+d}..{int(changed.max()):+d}) | "
        f"wet steps >=2: {int((steps >= 2).sum()):,}"
    )


if __name__ == "__main__":
    main()
