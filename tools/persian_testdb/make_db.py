"""Build a curated Persian test database that looks like a legacy Iranian ERP.

Deliberately hostile to an LLM agent, the way real systems are:

* no table or column comments, no declared foreign keys;
* Finglish (transliterated Persian) identifiers, some fully opaque (``f1``, ``c2``);
* Persian values, with the usual real-world noise: Arabic ي/ك mixed with
  Persian ی/ک, zero-width non-joiners, Persian digits in text columns;
* Jalali (Shamsi) dates stored as ``YYYY/MM/DD`` strings and as integers;
* status / type columns stored as numeric codes whose meaning lives in a
  generic lookup table;
* archive, temp and log tables as distractors.

Usage:
    python tools/persian_testdb/make_db.py --host 127.0.0.1 --user root --database pakhsh_db
"""

import argparse
import datetime as dt
import random

import pymysql


def to_jalali(d: dt.date) -> tuple[int, int, int]:
    gy, gm, gd = d.year, d.month, d.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    jy = 0 if gy <= 1600 else 979
    gy -= 621 if gy <= 1600 else 1600
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    jy += (days - 1) // 365
    if days > 365:
        days = (days - 1) % 365
    jm = 1 + days // 31 if days < 186 else 7 + (days - 186) // 30
    jd = 1 + (days % 31 if days < 186 else (days - 186) % 30)
    return jy, jm, jd


def jstr(d: dt.date) -> str:
    y, m, day = to_jalali(d)
    return f"{y:04d}/{m:02d}/{day:02d}"


def jint(d: dt.date) -> int:
    y, m, day = to_jalali(d)
    return y * 10000 + m * 100 + day


FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def arabize(s: str) -> str:
    """Swap Persian ی/ک for their Arabic look-alikes (common legacy-keyboard noise)."""
    return s.replace("ی", "ي").replace("ک", "ك")


OSTAN = ["تهران", "اصفهان", "فارس", "خراسان رضوی", "آذربایجان شرقی", "البرز", "خوزستان", "گیلان", "مازندران", "کرمان"]
SHAHR = {
    "تهران": ["تهران", "ری", "شهریار", "اسلامشهر"], "اصفهان": ["اصفهان", "کاشان", "نجف‌آباد"],
    "فارس": ["شیراز", "مرودشت", "کازرون"], "خراسان رضوی": ["مشهد", "نیشابور", "سبزوار"],
    "آذربایجان شرقی": ["تبریز", "مراغه"], "البرز": ["کرج", "فردیس"], "خوزستان": ["اهواز", "آبادان", "دزفول"],
    "گیلان": ["رشت", "بندر انزلی"], "مازندران": ["ساری", "بابل", "آمل"], "کرمان": ["کرمان", "رفسنجان"],
}
GOROH = {
    "لبنیات": ["شیر کم‌چرب", "ماست پرچرب", "پنیر لیقوان", "دوغ گازدار", "کره حیوانی", "خامه صبحانه"],
    "نوشیدنی": ["آب معدنی", "نوشابه کولا", "آبمیوه پرتقال", "چای سیاه", "قهوه فوری", "دلستر لیمو"],
    "خشکبار": ["پسته اکبری", "بادام درختی", "کشمش پلویی", "خرما مضافتی", "گردو", "انجیر خشک"],
    "شوینده": ["مایع ظرفشویی", "پودر لباسشویی", "شامپو", "صابون", "سفیدکننده", "نرم‌کننده"],
    "حبوبات و غلات": ["برنج طارم", "برنج هاشمی", "عدس", "نخود", "لوبیا چیتی", "ماکارونی"],
    "تنقلات": ["چیپس ساده", "پفک", "بیسکویت", "شکلات تلخ", "کیک صبحانه", "آدامس"],
    "کنسرو": ["تن ماهی", "رب گوجه‌فرنگی", "کنسرو لوبیا", "خیارشور", "زیتون", "مربا آلبالو"],
    "روغن": ["روغن آفتابگردان", "روغن زیتون", "روغن کنجد", "روغن سرخ‌کردنی"],
}
BRANDS = ["کاله", "میهن", "دامداران", "گلستان", "طبیعت", "سن‌ایچ", "چی‌توز", "مزمز", "لادن", "پرسیل", "تاژ", "شیرین‌عسل"]
NAM_M = ["علی", "محمد", "حسین", "رضا", "مهدی", "امیر", "سعید", "حمید", "کیوان", "یاسر", "کامران", "مجید"]
NAM_F = ["فاطمه", "زهرا", "مریم", "سارا", "نرگس", "کیمیا", "یگانه", "الهام", "نیلوفر", "مینا", "شیوا", "پریسا"]
FAMIL = ["محمدی", "حسینی", "احمدی", "کریمی", "رضایی", "موسوی", "جعفری", "کاظمی", "یزدانی", "اکبری", "صادقی", "رحیمی",
         "ملکی", "کیانی", "نیکنام", "شکوهی", "یوسفی", "قاسمی"]
SEMAT = ["مدیر شعبه", "فروشنده", "صندوقدار", "انباردار", "حسابدار", "راننده", "بازاریاب"]
NOE_HAZINE = ["اجاره", "حقوق و دستمزد", "حمل و نقل", "آب و برق و گاز", "تبلیغات", "تعمیر و نگهداری", "مالیات"]
# generic code table: (noe, code, sharh)
CODE_PAYE = [
    ("vaziat_faktor", 1, "تایید شده"), ("vaziat_faktor", 2, "در انتظار پرداخت"), ("vaziat_faktor", 3, "لغو شده"),
    ("vaziat_faktor", 4, "مرجوع شده"),
    ("noe_pardakht", 1, "نقدی"), ("noe_pardakht", 2, "کارت‌خوان"), ("noe_pardakht", 3, "چک"), ("noe_pardakht", 4, "نسیه"),
    ("noe_moshtari", 1, "مصرف‌کننده"), ("noe_moshtari", 2, "مغازه‌دار"), ("noe_moshtari", 3, "عمده‌فروش"),
    ("noe_gardesh", 1, "ورود"), ("noe_gardesh", 2, "خروج"), ("noe_gardesh", 3, "انتقال"), ("noe_gardesh", 4, "ضایعات"),
    ("elat_bargasht", 1, "تاریخ گذشته"), ("elat_bargasht", 2, "آسیب‌دیدگی"), ("elat_bargasht", 3, "اشتباه در سفارش"),
]

DDL = """
CREATE TABLE tbl_ostan (ostan_id INT PRIMARY KEY, ostan_name VARCHAR(60));
CREATE TABLE tbl_shahr (shahr_id INT PRIMARY KEY, shahr_name VARCHAR(60), ostan_id INT);
CREATE TABLE tbl_goroh_kala (goroh_id INT PRIMARY KEY, goroh_name VARCHAR(80), pedar_id INT);
CREATE TABLE tbl_tamin_konande (tk_id INT PRIMARY KEY, sherkat VARCHAR(120), shahr_id INT, tel VARCHAR(20), mohlat_pardakht INT);
CREATE TABLE tbl_kala (kala_id INT PRIMARY KEY, kala_name VARCHAR(160), goroh_id INT, tk_id INT, vahed VARCHAR(20),
  gh_kharid BIGINT, gh_forosh BIGINT, barcode VARCHAR(20), faal TINYINT);
CREATE TABLE tbl_code_paye (noe VARCHAR(30), code INT, sharh VARCHAR(80));
CREATE TABLE tbl_moshtari (moshtari_id INT PRIMARY KEY, nam VARCHAR(60), famil VARCHAR(60), code_melli VARCHAR(12),
  mobile VARCHAR(16), shahr_id INT, tarikh_ozviat VARCHAR(10), jens CHAR(1), noe INT, etebar BIGINT);
CREATE TABLE tbl_shobe (shobe_id INT PRIMARY KEY, shobe_name VARCHAR(80), shahr_id INT, tarikh_eftetah INT, metraj INT);
CREATE TABLE tbl_semat (semat_id INT PRIMARY KEY, onvan VARCHAR(60));
CREATE TABLE tbl_karmand (karmand_id INT PRIMARY KEY, nam VARCHAR(60), famil VARCHAR(60), semat_id INT, shobe_id INT,
  tarikh_estekhdam VARCHAR(10), hoghogh_paye BIGINT, faal TINYINT);
CREATE TABLE tbl_faktor (faktor_id INT PRIMARY KEY, moshtari_id INT, shobe_id INT, karmand_id INT, tarikh VARCHAR(10),
  saat VARCHAR(5), jam_kol BIGINT, takhfif BIGINT, maliat BIGINT, vaziat INT, noe_pardakht INT);
CREATE TABLE tbl_faktor_riz (riz_id INT PRIMARY KEY, faktor_id INT, kala_id INT, tedad INT, fi BIGINT, takhfif_riz BIGINT);
CREATE TABLE tbl_anbar (anbar_id INT PRIMARY KEY, anbar_name VARCHAR(80), shobe_id INT, zarfiat INT);
CREATE TABLE tbl_mojodi (anbar_id INT, kala_id INT, tedad INT, hadaghal INT, tarikh_beroz INT, PRIMARY KEY (anbar_id, kala_id));
CREATE TABLE tbl_gardesh_anbar (gardesh_id INT PRIMARY KEY, anbar_id INT, kala_id INT, noe INT, tedad INT, tarikh INT, sanad VARCHAR(20));
CREATE TABLE tbl_kharid (kharid_id INT PRIMARY KEY, tk_id INT, anbar_id INT, tarikh VARCHAR(10), jam_kol BIGINT, tasvie TINYINT);
CREATE TABLE tbl_kharid_riz (riz_id INT PRIMARY KEY, kharid_id INT, kala_id INT, tedad INT, fi BIGINT);
CREATE TABLE tbl_bargasht (bargasht_id INT PRIMARY KEY, faktor_id INT, kala_id INT, tedad INT, tarikh VARCHAR(10), elat INT, mablagh BIGINT);
CREATE TABLE tbl_noe_hazine (noe_id INT PRIMARY KEY, onvan VARCHAR(80));
CREATE TABLE tbl_hazine (hazine_id INT PRIMARY KEY, shobe_id INT, noe_id INT, mablagh BIGINT, tarikh VARCHAR(10), tozihat VARCHAR(200));
CREATE TABLE tbl_hoghogh (id INT PRIMARY KEY, karmand_id INT, sal INT, mah INT, paye BIGINT, ezafe_kar BIGINT, kosorat BIGINT, khales BIGINT);
CREATE TABLE t_prs01 (f1 INT PRIMARY KEY, f2 INT, f3 INT, f4 VARCHAR(10), f5 INT, f6 VARCHAR(200));
CREATE TABLE tbl_faktor_old_1399 LIKE tbl_faktor;
CREATE TABLE tmp_imp_kala (c1 VARCHAR(200), c2 VARCHAR(200), c3 VARCHAR(200), c4 VARCHAR(200));
CREATE TABLE tbl_log (log_id INT PRIMARY KEY, karbar VARCHAR(40), amal VARCHAR(40), zaman DATETIME, ip VARCHAR(40));
CREATE TABLE tbl_tanzimat (kelid VARCHAR(60) PRIMARY KEY, meghdar VARCHAR(200));
"""


def build(conn, seed: int = 7, n_cust: int = 1500, n_fakt: int = 12000):
    rnd = random.Random(seed)
    cur = conn.cursor()

    def ins(table, rows):
        if not rows:
            return
        ph = ",".join(["%s"] * len(rows[0]))
        for i in range(0, len(rows), 2000):
            cur.executemany(f"INSERT INTO {table} VALUES ({ph})", rows[i:i + 2000])

    for stmt in DDL.strip().split(";\n"):
        if stmt.strip():
            cur.execute(stmt)

    ins("tbl_ostan", [(i + 1, o) for i, o in enumerate(OSTAN)])
    shahr_rows, sid = [], 0
    for oi, o in enumerate(OSTAN):
        for s in SHAHR[o]:
            sid += 1
            # every 4th city is typed with Arabic letters, as legacy keyboards did
            shahr_rows.append((sid, arabize(s) if sid % 4 == 0 else s, oi + 1))
    ins("tbl_shahr", shahr_rows)
    n_shahr = len(shahr_rows)
    big = [r[0] for r in shahr_rows if r[1] in ("تهران", "مشهد", "اصفهان", "شیراز", "تبریز", "کرج", "اهواز")]

    ins("tbl_goroh_kala", [(i + 1, g, None) for i, g in enumerate(GOROH)])
    ins("tbl_tamin_konande", [
        (i + 1, f"شرکت پخش {b}", rnd.randint(1, n_shahr), f"021{rnd.randint(20000000, 89999999)}", rnd.choice([30, 45, 60, 90]))
        for i, b in enumerate(BRANDS)])

    kala, kid = [], 0
    for gi, (g, items) in enumerate(GOROH.items()):
        for it in items:
            for b in rnd.sample(BRANDS, 3):
                kid += 1
                buy = rnd.randrange(150_000, 6_000_000, 5_000)  # Rial
                name = f"{it} {b}"
                if kid % 7 == 0:
                    name = arabize(name)
                if kid % 9 == 0:
                    name += f" {rnd.choice(['۲۵۰', '۵۰۰', '۹۰۰'])} گرمی"
                kala.append((kid, name, gi + 1, BRANDS.index(b) + 1, rnd.choice(["عدد", "بسته", "کیلوگرم", "کارتن"]),
                             buy, int(buy * rnd.uniform(1.12, 1.45) // 1000 * 1000), str(rnd.randint(6260000000000, 6269999999999)),
                             0 if kid % 23 == 0 else 1))
    ins("tbl_kala", kala)
    n_kala = len(kala)
    price = {k[0]: k[6] for k in kala}

    ins("tbl_code_paye", CODE_PAYE)

    start, end = dt.date(2022, 3, 21), dt.date(2024, 9, 20)  # 1401/01/01 .. 1403/06/30
    span = (end - start).days

    def rdate():
        return start + dt.timedelta(days=rnd.randint(0, span))

    cust = []
    for i in range(1, n_cust + 1):
        male = rnd.random() < 0.55
        nam = rnd.choice(NAM_M if male else NAM_F)
        fam = rnd.choice(FAMIL)
        if i % 6 == 0:
            nam, fam = arabize(nam), arabize(fam)
        mob = f"09{rnd.randint(10, 39)}{rnd.randint(1000000, 9999999)}"
        if i % 5 == 0:
            mob = mob.translate(FA_DIGITS)
        cust.append((i, nam, fam, f"{rnd.randint(10**9, 10**10 - 1)}", mob,
                     rnd.choice(big) if rnd.random() < 0.7 else rnd.randint(1, n_shahr),
                     jstr(rdate()), "م" if male else "ز", rnd.choices([1, 2, 3], [70, 22, 8])[0],
                     rnd.choice([0, 0, 50_000_000, 200_000_000, 1_000_000_000])))
    ins("tbl_moshtari", cust)

    shobe = [(i + 1, f"شعبه {shahr_rows[c - 1][1]} {i + 1}", c, jint(dt.date(2015 + i % 6, 1 + i % 12, 10)), rnd.choice([80, 120, 200, 350]))
             for i, c in enumerate(big + big[:3])]
    ins("tbl_shobe", shobe)
    n_shobe = len(shobe)
    ins("tbl_semat", [(i + 1, s) for i, s in enumerate(SEMAT)])
    karmand = []
    for i in range(1, 81):
        male = rnd.random() < 0.6
        karmand.append((i, rnd.choice(NAM_M if male else NAM_F), rnd.choice(FAMIL), rnd.randint(1, len(SEMAT)),
                        rnd.randint(1, n_shobe), jstr(dt.date(2016, 1, 1) + dt.timedelta(days=rnd.randint(0, 2800))),
                        rnd.randrange(90_000_000, 260_000_000, 5_000_000), 0 if i % 11 == 0 else 1))
    ins("tbl_karmand", karmand)
    sellers = {s: [k[0] for k in karmand if k[4] == s] or [1] for s in range(1, n_shobe + 1)}

    fakt, riz, rid = [], [], 0
    for fid in range(1, n_fakt + 1):
        d = rdate()
        sh = rnd.randint(1, n_shobe)
        total = 0
        for _ in range(rnd.randint(1, 6)):
            rid += 1
            k = rnd.randint(1, n_kala)
            q = rnd.randint(1, 12)
            disc = rnd.choice([0, 0, 0, 5, 10]) * price[k] * q // 100
            riz.append((rid, fid, k, q, price[k], disc))
            total += price[k] * q - disc
        takh = rnd.choice([0, 0, 0, 2, 5]) * total // 100
        fakt.append((fid, rnd.randint(1, n_cust), sh, rnd.choice(sellers[sh]), jstr(d),
                     f"{rnd.randint(8, 21):02d}:{rnd.randint(0, 59):02d}", total, takh, (total - takh) * 9 // 100,
                     rnd.choices([1, 2, 3, 4], [82, 8, 6, 4])[0], rnd.choices([1, 2, 3, 4], [25, 55, 8, 12])[0]))
    ins("tbl_faktor", fakt)
    ins("tbl_faktor_riz", riz)

    anbar = [(i + 1, f"انبار {'مرکزی' if i == 0 else shobe[i - 1][1]}", None if i == 0 else i, rnd.choice([5000, 12000, 30000]))
             for i in range(n_shobe + 1)]
    ins("tbl_anbar", anbar)
    today = jint(end)
    ins("tbl_mojodi", [(a[0], k, rnd.choice([0, 0, rnd.randint(1, 30), rnd.randint(30, 900)]), rnd.choice([10, 20, 50]), today)
                       for a in anbar for k in range(1, n_kala + 1) if rnd.random() < 0.6])
    ins("tbl_gardesh_anbar", [(i, rnd.randint(1, len(anbar)), rnd.randint(1, n_kala), rnd.choices([1, 2, 3, 4], [35, 50, 10, 5])[0],
                               rnd.randint(1, 200), jint(rdate()), f"S-{rnd.randint(1000, 99999)}") for i in range(1, 8001)])

    kh, khr, rid = [], [], 0
    for i in range(1, 1201):
        total = 0
        for _ in range(rnd.randint(1, 8)):
            rid += 1
            k = rnd.randint(1, n_kala)
            q = rnd.randint(20, 500)
            khr.append((rid, i, k, q, kala[k - 1][5]))
            total += q * kala[k - 1][5]
        kh.append((i, rnd.randint(1, len(BRANDS)), rnd.randint(1, len(anbar)), jstr(rdate()), total, rnd.choice([0, 1, 1])))
    ins("tbl_kharid", kh)
    ins("tbl_kharid_riz", khr)

    ret = []
    for i, f in enumerate([f for f in fakt if f[9] == 4], start=1):
        k = rnd.randint(1, n_kala)
        q = rnd.randint(1, 4)
        ret.append((i, f[0], k, q, f[4], rnd.randint(1, 3), price[k] * q))
    ins("tbl_bargasht", ret)

    ins("tbl_noe_hazine", [(i + 1, h) for i, h in enumerate(NOE_HAZINE)])
    ins("tbl_hazine", [(i, rnd.randint(1, n_shobe), rnd.randint(1, len(NOE_HAZINE)), rnd.randrange(5_000_000, 900_000_000, 500_000),
                        jstr(rdate()), "") for i in range(1, 3001)])
    hog, hid = [], 0
    for k in karmand:
        for sal in (1401, 1402, 1403):
            for mah in range(1, 13 if sal < 1403 else 7):
                hid += 1
                ez, ko = rnd.randrange(0, 40_000_000, 1_000_000), rnd.randrange(5_000_000, 25_000_000, 500_000)
                hog.append((hid, k[0], sal, mah, k[6], ez, ko, k[6] + ez - ko))
    ins("tbl_hoghogh", hog)

    # Fully opaque table: customer visits by sales reps (f2=karmand, f3=moshtari, f4=date, f5=result code, f6=note).
    ins("t_prs01", [(i, rnd.randint(1, 80), rnd.randint(1, n_cust), jstr(rdate()), rnd.randint(0, 2),
                     rnd.choice(["سفارش گرفته شد", "مشتری حضور نداشت", "پیگیری مجدد", "عدم تمایل به خرید"]))
                    for i in range(1, 2501)])

    old = dt.date(2020, 3, 20)
    ins("tbl_faktor_old_1399", [(i, rnd.randint(1, n_cust), rnd.randint(1, n_shobe), rnd.randint(1, 80),
                                 jstr(old + dt.timedelta(days=rnd.randint(0, 364))), "12:00", rnd.randrange(10**6, 10**8, 10**4), 0, 0, 1, 1)
                                for i in range(1, 2001)])
    ins("tmp_imp_kala", [(k[1], str(k[5]), k[7], "ok") for k in kala[:40]])
    ins("tbl_log", [(i, rnd.choice(["admin", "anbardar1", "sandogh2"]), rnd.choice(["login", "logout", "print", "edit"]),
                     dt.datetime(2024, 1, 1) + dt.timedelta(minutes=rnd.randint(0, 300000)), f"192.168.1.{rnd.randint(2, 250)}")
                    for i in range(1, 3001)])
    ins("tbl_tanzimat", [("nerkh_maliat", "9"), ("vahed_pol", "ریال"), ("nam_sherkat", "شرکت پخش مواد غذایی نمونه"), ("version", "3.8.1")])
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="")
    ap.add_argument("--database", default="pakhsh_db")
    ap.add_argument("--unix-socket", default=None)
    a = ap.parse_args()
    kw = dict(user=a.user, password=a.password, charset="utf8mb4", autocommit=False)
    if a.unix_socket:
        kw["unix_socket"] = a.unix_socket
    else:
        kw.update(host=a.host, port=a.port)
    conn = pymysql.connect(**kw)
    cur = conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS `{a.database}`")
    cur.execute(f"CREATE DATABASE `{a.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
    cur.execute(f"USE `{a.database}`")
    build(conn)
    cur.execute("SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema=%s ORDER BY 1", (a.database,))
    for name, rows in cur.fetchall():
        print(f"{name:24s} ~{rows}")
    conn.close()


if __name__ == "__main__":
    main()
