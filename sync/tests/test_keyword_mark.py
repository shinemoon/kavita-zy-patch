"""keyword_mark 单测 (离线, 不碰 Kavita / 掌阅)

覆盖:
  - normalize: 坏字段清洗 / 目录白名单 / 样式清洗 (防 CSS 注入) / bind 归一
  - 语料解析: epub-refiner 格式 + translator-skill mappings 格式
  - 选语料: 显式 bind / bind 到绝对路径 / 名字匹配 (只认同名或高度相似) /
            同一个作者的另一本不能误配
  - payload: 分类过滤 only / 样式关掉 / stop 词 / min_len / 纯数字 / 去重 /
             最长词优先留给前端 / max_terms 截断
"""
import json
import os
import sys
import tempfile
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ["ZY_PATCH_ROOT"] = tempfile.mkdtemp(prefix="zwroot-")

from sync import keyword_mark as km  # noqa: E402

FAIL = []


def eq(got, want, label):
    if got != want:
        FAIL.append(f"{label}: got={got!r} want={want!r}")


def ok(cond, label):
    if not cond:
        FAIL.append(f"{label}: false")


# ---- 语料样本 -------------------------------------------------------------

def write_corpus(dirp: pathlib.Path, name: str, title: str, cats: dict) -> pathlib.Path:
    p = dirp / name
    data = {
        "meta": {"title": title, "author": "Sue Townsend"},
        "categories": {cid: {"name": cn, "items": [{"term": t, "aliases": a} for t, a in items]}
                       for cid, (cn, items) in cats.items()},
        "index": {},
    }
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


D1 = pathlib.Path(tempfile.mkdtemp(prefix="kw-dir1-"))
D2 = pathlib.Path(tempfile.mkdtemp(prefix="kw-dir2-"))
ADRIAN = write_corpus(D1, "Adrian_Mole_1982-d5028a.json", "Adrian Mole 1982",
                      {"person": ("人名", [("Pandora", ["潘多拉", "P."]),
                                           ("mother", ["Mum", "mummy"]),
                                           ("it", [])]),
                       "place": ("地点", [("School", []), ("1981", [])]),
                       "org": ("组织", [("BBC", [])])})
OTHER = write_corpus(D2, "Adrian_Mole_WMD-abc123.json",
                     "Adrian Mole and the Weapons of Mass Destruction",
                     {"person": ("人名", [("Adrian", []), ("Pandora", ["潘多拉"])])})
MAPS = D2 / "terms.json"
MAPS.write_text(json.dumps({"mappings": [
    {"source": "Midlands Today", "target": "中部地区今日新闻", "tags": ["media"]},
    {"source": "Habitat", "target": "哈比塔家居", "tags": ["brand"]},
]}, ensure_ascii=False), encoding="utf-8")
(D2 / "index.json").write_text("{}", encoding="utf-8")


# ---- normalize ------------------------------------------------------------

base = km.default_conf()
eq(base["mode"], "highlight", "默认模式 highlight")
ok("stop" in base and len(base["stop"]) > 10, "默认带 stop 词表")

n = km.normalize({"mode": "SPAN", "dirs": ["/tmp", "relative", "/tmp"], "version": "7",
                  "styles": {"person": {"color": "red;background:url(x)", "bg": "#fff"},
                             "bogus": {"bg": "rgb(1,2,3)"}}})
eq(n["mode"], "span", "mode 大小写归一")
eq(n["dirs"], ["/tmp"], "dirs 只留绝对路径并去重")
eq(n["version"], 7, "version 转 int")
eq(n["styles"]["person"]["color"], "", "非法颜色被丢弃 (防注入)")
eq(n["styles"]["person"]["bg"], "#fff", "合法颜色保留")
ok("bogus" in n["styles"], "自定义分类样式保留")
ok("person" in n["styles"] and "place" in n["styles"], "默认 8 类兜底")

n2 = km.normalize({"bind": {"25": "a.json", "x": "b.json", "0": "c.json", "26": ""}})
eq(n2["bind"], {"25": "a.json"}, "bind 只留正整数 key + 非空 value")

n3 = km.normalize({"stop": []})
eq(n3["stop"], [], "显式空 stop 被尊重")
n4 = km.normalize({"min_len": 99, "max_terms": 1})
eq(n4["min_len"], 12, "min_len 上限夹住")
eq(n4["max_terms"], 50, "max_terms 下限夹住")

bad = pathlib.Path(os.environ["ZY_PATCH_ROOT"]) / "state" / km.CONF_NAME
bad.parent.mkdir(parents=True, exist_ok=True)
bad.write_text("{not json", encoding="utf-8")
eq(km.load()["enabled"], False, "配置坏了 → 关闭而不是乱高亮")
conf = km.save({"enabled": True, "dirs": [str(D1), str(D2)]})
eq(conf["version"], 2, "save 让 version 自增")
eq(km.load()["version"], 2, "save 后能读回")
eq(int(os.stat(bad).st_mode & 0o777), 0o644, "落盘权限 644 (5012 是 root)")

# ---- 扫语料 / 选语料 -------------------------------------------------------

corp = km.scan_corpora([str(D1), str(D2)])
files = sorted(c["file"] for c in corp)
eq(files, ["Adrian_Mole_1982-d5028a.json", "Adrian_Mole_WMD-abc123.json", "terms.json"],
   "扫描跳过 index.json, 认 mappings 格式")

conf = km.normalize({"dirs": [str(D1), str(D2)]})
c, why = km.resolve(25, "Adrian Mole 1982", conf)
eq((c or {}).get("file"), "Adrian_Mole_1982-d5028a.json", "名字完全相等 → 选中")
eq(why["how"], "name", "命中方式 = name")

c, why = km.resolve(25, "Adrian Mole and the Weapons of Mass Destruction", conf)
eq((c or {}).get("file"), "Adrian_Mole_WMD-abc123.json", "同名优先精确命中那一份")

# 同一个作者的另一本 (只留 1982 的语料) 不能被误配上去
c, why = km.resolve(25, "Adrian Mole and the Weapons of Mass Destruction",
                    km.normalize({"dirs": [str(D1)]}))
eq(c, None, "同一作者的另一本不能误配 (宁可让人手动绑)")

c, why = km.resolve(25, "", conf)
eq(c, None, "系列名空 → 不猜")

conf_b = km.normalize({"dirs": [str(D1), str(D2)],
                       "bind": {"25": "Adrian_Mole_WMD-abc123.json"}})
c, why = km.resolve(25, "随便", conf_b)
eq((c or {}).get("file"), "Adrian_Mole_WMD-abc123.json", "显式 bind 优先")
eq(why["how"], "bind", "bind 命中方式")

c, why = km.resolve(25, "x", km.normalize({"dirs": [str(D1)], "bind": {"25": "nope.json"}}))
eq((c, why["how"]), (None, "bind_missing"), "bind 指向不存在的文件 → 报 bind_missing")

c, why = km.resolve(25, "x", km.normalize({"dirs": ["/nonexistent"], "bind": {"25": str(ADRIAN)}}))
eq((c or {}).get("file"), ADRIAN.name, "bind 到绝对路径 (不在扫描目录里) 也能读")

# ---- payload --------------------------------------------------------------

conf = km.normalize({"dirs": [str(D1), str(D2)], "enabled": True,
                     "bind": {"25": ADRIAN.name}})
p = km.build_payload(25, "whatever", conf)
eq(p["total"], sum(len(x["words"]) for x in p["cats"]), "total = 各分类词数之和")
by = {x["id"]: x["words"] for x in p["cats"]}
ok("Pandora" in by["person"], "term 进词表")
ok("潘多拉" in by["person"], "alias 进词表")
ok("P." not in by["person"], "单字母 + 标点 (alnum<2) 被丢")
ok("it" not in by["person"], "stop 词被丢")
ok("Mum" in by["person"] and "mummy" in by["person"], "大小写不同的词各留一份")
ok("1981" not in by.get("place", []), "纯数字词被丢 (skip_digits)")
ok(by["person"].count("Pandora") == 1, "同分类内去重")

p2 = km.build_payload(25, "whatever", km.normalize({"dirs": [str(D1)], "enabled": True,
                                                    "bind": {"25": ADRIAN.name},
                                                    "skip_digits": False}))
ok("1981" in {w for x in p2["cats"] for w in x["words"]}, "skip_digits=False 时数字词保留")

p3 = km.build_payload(25, "x", km.normalize({"dirs": [str(D1)], "bind": {"25": ADRIAN.name},
                                             "only": ["org"]}))
eq([x["id"] for x in p3["cats"]], ["org"], "only 只留指定分类")

p4 = km.build_payload(25, "x", km.normalize({"dirs": [str(D1)], "bind": {"25": ADRIAN.name},
                                             "styles": {"org": {"enabled": False}}}))
eq([x["id"] for x in p4["cats"] if x["id"] == "org"], [], "分类样式关掉就不下发给前端")

p5 = km.build_payload(25, "x", km.normalize({"dirs": [str(D1)], "bind": {"25": ADRIAN.name},
                                             "enabled": False}))
eq((p5["enabled"], p5.get("cats")), (False, None), "总开关关掉 → 直接返 enabled=false")

p6 = km.build_payload(25, "x", km.normalize({"dirs": [str(D1)], "bind": {"25": ADRIAN.name},
                                             "max_terms": 50}))
ok(p6["total"] <= 50, "max_terms 截断生效")

# ---- 预算公平分配 (2026-10-31 修: 人名吃光 max_terms, 道具/地点一个词都分不到) ----
D3 = pathlib.Path(tempfile.mkdtemp(prefix="kw-dir3-"))
BIG = write_corpus(D3, "Big_Book-big001.json", "Big Book", {
    "person": ("人名", [("人物%d" % i, ["别名%d" % i]) for i in range(400)]),
    "place": ("地点", [("地点%d" % i, []) for i in range(60)]),
    "object": ("道具", [("道具%d" % i, []) for i in range(60)]),
})
pf = km.build_payload(25, "x", km.normalize({"dirs": [str(D3)], "bind": {"25": BIG.name},
                                             "max_terms": 90}))
cat_ids = [x["id"] for x in pf["cats"]]
eq(sorted(cat_ids), ["object", "person", "place"], "大分类占满预算时其他分类仍要出现")
ok(all(len(x["words"]) > 0 for x in pf["cats"]), "每类都分到词 (不再被第一个分类吃光)")
eq(pf["total"], sum(len(x["words"]) for x in pf["cats"]), "公平分配后 total = 各分类之和")
ok(pf["total"] <= 90, "公平分配不超过 max_terms")
eq({x["id"]: len(x["words"]) for x in pf["cats"]}["place"], 30,
   "小分类拿到均分额度 (90/3=30)")
eq({x["id"]: len(x["words"]) for x in pf["cats"]}["person"], 30,
   "大分类先拿均分, 不吃别人的额度")

# count 降序: refiner 给的 count 决定截断顺序
D4 = pathlib.Path(tempfile.mkdtemp(prefix="kw-dir4-"))
COUNTED = D4 / "counted.json"
COUNTED.write_text(json.dumps({
    "meta": {"title": "Counted"},
    "categories": {"person": {"name": "人名", "items": [
        {"term": "冷门甲", "aliases": [], "count": 1},
        {"term": "热门乙", "aliases": [], "count": 999},
        {"term": "中频丙", "aliases": [], "count": 50},
    ]}},
}, ensure_ascii=False), encoding="utf-8")
pc = km.build_payload(25, "x", km.normalize({"dirs": [str(D4)], "bind": {"25": "counted.json"}}))
eq({x["id"]: x["words"] for x in pc["cats"]}["person"], ["热门乙", "中频丙", "冷门甲"],
   "词按 count 降序排 (截断时先砍冷门词)")

p7 = km.build_payload(99, "没有这本书", km.normalize({"dirs": [str(D1)]}))
eq((p7["cats"], p7["total"], p7["pick"]["how"]), ([], 0, "none"), "没匹配 → 空词表不报错")

# 序列化体积 (前端会整包吃下去, 别悄悄膨胀)
ok(len(json.dumps(p, ensure_ascii=False)) < 200000, "payload 体积可控")

# mappings 格式的 tags 第一个当分类 id
p8 = km.build_payload(25, "x", km.normalize({"dirs": [str(D2)], "bind": {"25": "terms.json"}}))
ids = {x["id"]: x["words"] for x in p8["cats"]}
ok("Habitat" in ids.get("brand", []), "mappings 格式: source 进词表")
ok("哈比塔家居" in ids.get("brand", []), "mappings 格式: target 也进词表 (双语都能高亮)")

print("FAIL" if FAIL else "PASS", len(FAIL))
for f in FAIL:
    print("  -", f)
sys.exit(1 if FAIL else 0)
