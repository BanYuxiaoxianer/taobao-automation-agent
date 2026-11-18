"""示例知识库生成（深加工版 / by 31）

上游把商品库写死在代码里的 JSON 常量，换品类就没东西查。
这里给一份**能直接改**的示例数据，跑一次就落盘到 data/knowledge/，
用户照着格式替换成自己的商品即可（也支持从电商后台导出的 CSV 再补）。

用法：python -m seed          生成（已存在则跳过）
      python -m seed --force  覆盖重写
"""

from __future__ import annotations

import json
import os
import sys

from config import DATA_DIR

KB = os.path.join(DATA_DIR, "knowledge")

PRODUCTS = [
    {
        "name": "304 不锈钢保温杯 500ml",
        "category": "家居日用",
        "desc": "双层真空，6 小时保温 65℃，杯口无缝，可放车载杯架，重 320g",
        "price": 79.0, "cost": 32.0,
        "selling_points": ["6 小时 65℃ 保温", "304 食品级内胆", "杯盖可拆洗"],
        "keywords": ["保温杯", "不锈钢保温杯", "车载水杯", "办公室水杯"],
        "aliases": ["发货", "快递", "什么时候到", "多久能到"],
    },
    {
        "name": "纯棉短袖 T 恤 男款 260g",
        "category": "服饰鞋包",
        "desc": "260g 重磅纯棉，双针车线不易变形，无感领口标签，适合日常通勤",
        "price": 89.0, "cost": 36.0,
        "selling_points": ["260g 重磅不透", "无感标签不磨脖", "缩水率 < 3%"],
        "keywords": ["重磅t恤", "纯棉短袖", "男t恤", "基础款上衣"],
        "aliases": ["尺码", "码数", "偏大偏小", "怎么选码"],
    },
    {
        "name": "桌面收纳盒 三层抽屉式",
        "category": "家居日用",
        "desc": "ABS 材质，单层承重 3kg，尺寸 28×18×22cm，适合书桌与化妆台",
        "price": 59.0, "cost": 21.0,
        "selling_points": ["单层承重 3kg", "抽屉导轨顺滑", "免安装开箱即用"],
        "keywords": ["桌面收纳", "抽屉收纳盒", "化妆品收纳", "书桌整理"],
        "aliases": ["尺寸", "多大", "能不能放", "容量"],
    },
]

FAQ = [
    {"q": "什么时候发货", "a": "付款后 48 小时内发出，江浙沪通常 2-3 天到，偏远地区 4-6 天。"},
    {"q": "发什么快递", "a": "默认发中通或圆通，需要顺丰可以补差价，下单前在备注里写。"},
    {"q": "支持七天无理由吗", "a": "支持。不影响二次销售即可退，7 天内申请，我们承担退回运费。"},
    {"q": "可以改地址吗", "a": "未发货可以改，把新地址发我，我核对后帮您改；已发货需要联系快递中转。"},
    {"q": "质量有问题怎么办", "a": "收到后 48 小时内拍照说明，经核实可直接补发或退款，运费我们承担。"},
    {"q": "能开发票吗", "a": "可以开电子普通发票，下单后留言备注抬头和税号即可。"},
    {"q": "保温杯能放多久热水", "a": "常温下装 95℃ 热水，6 小时后仍有 65℃ 左右，冬天会长一点。"},
    {"q": "T 恤会不会缩水", "a": "做过预缩处理，冷水手洗缩水率在 3% 以内，不建议烘干。"},
]

SUPPLIERS = [
    {"name": "义乌万腾日用百货", "category": "家居日用", "price": 21.5, "moq": 50,
     "region": "浙江义乌", "note": "支持混批，打样费 30 元可抵扣，3 天出货"},
    {"name": "永康不锈钢制品厂", "category": "家居日用", "price": 28.0, "moq": 100,
     "region": "浙江永康", "note": "有食品接触材料检测报告，可开票"},
    {"name": "广州十三行服装档口", "category": "服饰鞋包", "price": 32.0, "moq": 30,
     "region": "广东广州", "note": "断码款便宜，同款不同色可混批"},
    {"name": "南通家纺产业园", "category": "家纺", "price": 45.0, "moq": 20,
     "region": "江苏南通", "note": "纯棉面料源头，支持来图打样"},
]

TEMPLATES = {
    "家居日用": {
        "titles": [
            "304不锈钢保温杯 6小时保温 车载便携",
            "保温杯 304内胆 杯盖可拆洗 办公室水杯",
            "大容量保温杯 冬天热水 学生通勤杯",
        ],
        "detail_skeleton": ["使用场景痛点", "材质与参数", "清洁与保养", "售后承诺"],
        "category_path": "家居日用 > 杯具 > 保温杯",
    },
    "服饰鞋包": {
        "titles": [
            "260g重磅纯棉短袖 不透不闷 男款",
            "基础款T恤 无感标签 缩水率3%以内",
            "夏季短袖 重磅纯棉 通勤百搭",
        ],
        "detail_skeleton": ["面料与克重", "尺码表与版型", "洗涤说明", "退换政策"],
        "category_path": "服饰鞋包 > 男装 > T恤",
    },
}


def _write(path: str, payload, force: bool) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and not force:
        return f"跳过 {os.path.basename(path)}（已存在，加 --force 覆盖）"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return f"写入 {os.path.basename(path)}（{len(payload)} 条）"


def generate(force: bool = False) -> list[str]:
    os.makedirs(KB, exist_ok=True)
    return [
        _write(os.path.join(KB, "products.json"), PRODUCTS, force),
        _write(os.path.join(KB, "faq.json"), FAQ, force),
        _write(os.path.join(KB, "suppliers.json"), SUPPLIERS, force),
        _write(os.path.join(KB, "templates.json"), TEMPLATES, force),
    ]


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    for line in generate(force="--force" in sys.argv):
        print(line)
