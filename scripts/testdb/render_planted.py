"""Render planted test databases for MySQL and PostgreSQL from one source.

Single source of truth: every column formula is an E object carrying both
Python semantics (.py, used by verification) and per-dialect SQL with
correct parentheses by construction. Run:

  python scripts/testdb/render_planted.py

Regenerates mysql_planted.sql and postgres_planted.sql in place.
Stdlib only.
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent


class E:
    def __init__(self, py, my, pg):
        self.py, self.my, self.pg = py, my, pg

    def sql(self, d, iv):
        return (self.my if d == "my" else self.pg).replace("@I@", iv)


def C(v):
    if isinstance(v, str):
        e = v.replace("'", "''")
        return E(lambda i, v=v: v, "'" + e + "'", "'" + e + "'")
    return E(lambda i, v=v: v, str(v), str(v))


def S(s):
    return C(s)


def IV():
    return E(lambda i: i, "@I@", "@I@")


def J60():
    return E(lambda i: ((i - 1) % 60) + 1, "(((@I@-1)%60)+1)", "(((@I@-1)%60)+1)")


def _wrapped(t):
    return t == "@I@" or (t.startswith("(") and t.endswith(")"))


def _req(*ts):
    for t in ts:
        assert _wrapped(t), "unparenthesized base in expression builder: %s" % t


def cyc(b, mult, mod):
    _req(b.my, b.pg)
    t = "(((" + b.my + "*%d)%%%d)+1)" % (mult, mod)
    p = "(((" + b.pg + "*%d)%%%d)+1)" % (mult, mod)
    return E(lambda i, b=b, m=mult, d=mod: ((b.py(i) * m) % d) + 1, t, p)


def cyc0(b, mult, mod):
    _req(b.my, b.pg)
    t = "((" + b.my + "*%d)%%%d)" % (mult, mod)
    p = "((" + b.pg + "*%d)%%%d)" % (mult, mod)
    return E(lambda i, b=b, m=mult, d=mod: (b.py(i) * m) % d, t, p)


def dmod(b, mult, prime, mod):
    _req(b.my, b.pg)
    t = "((((" + b.my + "*%d)%%%d)%%%d)+1)" % (mult, prime, mod)
    p = "((((" + b.pg + "*%d)%%%d)%%%d)+1)" % (mult, prime, mod)
    return E(lambda i, b=b, m=mult, q=prime, d=mod: (((b.py(i) * m) % q) % d) + 1, t, p)


def dmod0(b, mult, prime, mod):
    _req(b.my, b.pg)
    t = "(((" + b.my + "*%d)%%%d)%%%d)" % (mult, prime, mod)
    p = "(((" + b.pg + "*%d)%%%d)%%%d)" % (mult, prime, mod)
    return E(lambda i, b=b, m=mult, q=prime, d=mod: (((b.py(i) * m) % q) % d), t, p)


def sub1(b):
    return E(lambda i, b=b: b.py(i) - 1, "(" + b.my + "-1)", "(" + b.pg + "-1)")


def add(a, b):
    return E(lambda i: a.py(i) + b.py(i), "(" + a.my + "+" + b.my + ")",
             "(" + a.pg + "+" + b.pg + ")")


def sub(a, b):
    return E(lambda i: a.py(i) - b.py(i), "(" + a.my + "-" + b.my + ")",
             "(" + a.pg + "-" + b.pg + ")")


def mul(a, b):
    return E(lambda i: a.py(i) * b.py(i), "(" + a.my + "*" + b.my + ")",
             "(" + a.pg + "*" + b.pg + ")")


def modE(a, k):
    return E(lambda i: a.py(i) % k, "(" + a.my + "%" + str(k) + ")",
             "(" + a.pg + "%" + str(k) + ")")


def divint(a, k):
    return E(lambda i: a.py(i) // k, "(" + a.my + " DIV " + str(k) + ")",
             "(" + a.pg + "/" + str(k) + ")")


def concat(*args):
    return E(lambda i: "".join(str(a.py(i)) for a in args),
             "CONCAT(" + ", ".join(a.my for a in args) + ")",
             "CONCAT(" + ", ".join(a.pg for a in args) + ")")


def _lpad_str(x, n, pad="0"):
    s = str(x)
    return s if len(s) == n else (s.rjust(n, pad) if len(s) < n else s[-n:])


def lpad(a, n, pad="0"):
    return E(lambda i: _lpad_str(a.py(i), n, pad),
             "LPAD(" + a.my + "," + str(n) + ",'" + pad + "')",
             "LPAD((" + a.pg + ")::text," + str(n) + ",'" + pad + "')")


def case_in(vals, then_, else_):
    v = ",".join(map(str, vals))
    return E(lambda i: then_.py(i) if i in vals else else_.py(i),
             "CASE WHEN @I@ IN (" + v + ") THEN " + then_.my + " ELSE " + else_.my + " END",
             "CASE WHEN @I@ IN (" + v + ") THEN " + then_.pg + " ELSE " + else_.pg + " END")


def case_lt(cond, k, then_, else_):
    return E(lambda i: then_.py(i) if cond.py(i) < k else else_.py(i),
             "CASE WHEN " + cond.my + "<" + str(k) + " THEN " + then_.my + " ELSE " + else_.my + " END",
             "CASE WHEN " + cond.pg + "<" + str(k) + " THEN " + then_.pg + " ELSE " + else_.pg + " END")


def case_null(modn, k, inner):
    return E(lambda i: None if (i % modn) < k else inner.py(i),
             "CASE WHEN (@I@%" + str(modn) + ")<" + str(k) + " THEN NULL ELSE " + inner.my + " END",
             "CASE WHEN (@I@%" + str(modn) + ")<" + str(k) + " THEN NULL ELSE " + inner.pg + " END")


def case_z(e, t="Y", f="N"):
    return E(lambda i: t if e.py(i) == 0 else f,
             "CASE WHEN (" + e.my + ")=0 THEN '" + t + "' ELSE '" + f + "' END",
             "CASE WHEN (" + e.pg + ")=0 THEN '" + t + "' ELSE '" + f + "' END")


def case_parity(a, b):
    return E(lambda i: a.py(i) if i % 2 == 1 else b.py(i),
             "CASE WHEN @I@%2=1 THEN " + a.my + " ELSE " + b.my + " END",
             "CASE WHEN @I@%2=1 THEN " + a.pg + " ELSE " + b.pg + " END")


def case_grade(e):
    return E(lambda i: "ABCD"[e.py(i) % 4],
             "CASE ((" + e.my + ")%4) WHEN 0 THEN 'A' WHEN 1 THEN 'B' WHEN 2 THEN 'C' ELSE 'D' END",
             "CASE ((" + e.pg + ")%4) WHEN 0 THEN 'A' WHEN 1 THEN 'B' WHEN 2 THEN 'C' ELSE 'D' END")


def dateoff(base, e0):
    return E(lambda i: (base, e0.py(i)),
             "DATE_ADD('" + base + "', INTERVAL " + e0.my + " DAY)",
             "DATE '" + base + "' + " + e0.pg)


def price_of(base):
    return E(lambda i: (((base.py(i) * 13) % 400) / 100) + 5,
             "((((" + base.my + "*13)%400)/100)+5)",
             "((((" + base.pg + "*13)%400)/100.0)+5)")


def md5iv():
    return E(lambda i: "md5-%d" % i, "MD5(@I@)", "md5((@I@)::text)")


def repeat_lorem():
    inner = concat(S("lorem-"), cyc0(IV(), 1, 7), S(" "))
    return E(lambda i: ("lorem-%d " % (i % 7)) * 30,
             "REPEAT(" + inner.my + ", 30)", "repeat(" + inner.pg + ", 30)")


def repeat_bin():
    inner = concat(S("bin-"), cyc0(IV(), 1, 5), S(";"))
    return E(lambda i: ("bin-%d;" % (i % 5)) * 40,
             "REPEAT(" + inner.my + ", 40)",
             "convert_to(repeat(" + inner.pg + ", 40), 'UTF8')")


def json_k():
    inner = concat(S('{"k": '), cyc0(IV(), 1, 5), S("}"))
    return E(lambda i: '{"k": %d}' % (i % 5), inner.my, "(" + inner.pg + ")::json")


# Shared fillers -------------------------------------------------------
def city_e(b=None, mult=19, prime=83, mod=8, width=2):
    b = b or IV()
    return concat(S("City_"), lpad(dmod(b, mult, prime, mod), width))


def status_e(b=None, mult=17, prime=89, mod=4):
    b = b or IV()
    return concat(S("ST_"), dmod(b, mult, prime, mod))


def note_e(b=None, mult=37, prime=71, mod=200):
    b = b or IV()
    return concat(S("N"), dmod(b, mult, prime, mod))


def cust_name_e(cid):
    return concat(S("Customer_"), lpad(cid, 3))


def cust_email_e(cid):
    return concat(S("c"), lpad(cid, 3), S("@example.com"))


# Table registry -------------------------------------------------------
TABLES = []


def table(name, cols, n, exprs, pk_extra="", comment="", from_hook=None):
    TABLES.append({"name": name, "cols": cols, "n": n, "exprs": exprs,
                   "pk_extra": pk_extra, "comment": comment, "from_hook": from_hook})


def define_tables():
    if TABLES:
        return
    b = IV()
    # ---- 1NF defects ----
    j = J60()
    table("t_1nf_nokey",
          [("rec_id", "INT"), ("cust_code", "INT"), ("prod_code", "INT"),
           ("qty", "INT"), ("price", "DECIMAL(10,2)"), ("order_day", "INT"),
           ("region", "VARCHAR(8)"), ("channel", "VARCHAR(8)"),
           ("flag", "CHAR(1)"), ("note_txt", "VARCHAR(60)")],
          120,
          [cyc(j, 3, 50), cyc(j, 7, 20), cyc(j, 11, 15), cyc(j, 13, 9),
           add(add(dmod(j, 17, 45, 45), C(10)), C(0.99)),
           cyc(j, 19, 28),
           concat(S("R"), cyc(j, 23, 4)), concat(S("CH"), cyc(j, 29, 3)),
           case_z(cyc0(j, 31, 2)),
           concat(S("Note_"), lpad(dmod(j, 37, 71, 50), 3))],
          comment="t_1nf_nokey: no key at all. Rows 61..120 duplicate rows 1..60.",
          from_hook="jblock")

    cid = cyc(b, 7, 40)
    table("t_1nf_delimited",
          [("order_id", "INT PRIMARY KEY"), ("customer_id", "INT"),
           ("tags", "VARCHAR(60)"), ("qty", "INT"), ("order_date", "DATE"),
           ("status", "VARCHAR(8)"), ("ship_city", "VARCHAR(12)"),
           ("flag", "CHAR(1)"), ("note_txt", "VARCHAR(60)")],
          150,
          [b, cid,
           case_lt(cyc0(b, 7, 10), 7,
                   concat(S("tag"), add(cyc0(b, 1, 5), C(1)), S(",tag"),
                          add(cyc0(add(b, C(2)), 1, 5), C(1))),
                   concat(S("tag"), add(cyc0(b, 1, 5), C(1)))),
           dmod(b, 13, 97, 5), dateoff("2024-01-05", cyc0(b, 23, 79)),
           status_e(), city_e(), case_z(dmod0(b, 31, 67, 2)), note_e()],
          comment="t_1nf_delimited: tags holds comma-separated lists in ~70% of rows.")

    table("t_1nf_repeating",
          [("cust_id", "INT PRIMARY KEY"), ("cust_name", "VARCHAR(20)"),
           ("phone1", "VARCHAR(16)"), ("phone2", "VARCHAR(16)"),
           ("phone3", "VARCHAR(16)"), ("email", "VARCHAR(40)"),
           ("city", "VARCHAR(12)"), ("signup_date", "DATE"),
           ("status", "VARCHAR(8)"), ("note_txt", "VARCHAR(60)")],
          150,
          [b, concat(S("Cust_"), lpad(b, 4)),
           concat(S("555-01"), lpad(add(cyc0(b, 1, 50), C(1)), 2)),
           concat(S("555-02"), lpad(cyc(b, 7, 50), 2)),
           concat(S("555-03"), lpad(cyc(b, 11, 50), 2)),
           concat(S("cust"), b, S("@example.com")),
           city_e(), dateoff("2023-06-01", cyc0(b, 23, 79)),
           status_e(), note_e()],
          comment="t_1nf_repeating: phone1, phone2, phone3 repeating group.")

    # ---- 2NF defects ----
    o = cyc(b, 7, 60)
    p = cyc(b, 11, 50)
    table("t_2nf_partial",
          [("order_id", "INT"), ("product_id", "INT"),
           ("product_name", "VARCHAR(20)"), ("unit_price", "DECIMAL(10,2)"),
           ("qty", "INT"), ("order_date", "DATE"), ("ship_mode", "VARCHAR(8)"),
           ("warehouse", "VARCHAR(8)"), ("priority", "VARCHAR(8)"),
           ("note_txt", "VARCHAR(60)")],
          300,
          [o, p, concat(S("Product_"), lpad(p, 3)), price_of(p),
           dmod(b, 13, 97, 5),
           dateoff("2024-02-01", modE(mul(o, C(3)), 28)),
           concat(S("SM_"), dmod(b, 19, 83, 4)),
           concat(S("WH_"), dmod(b, 23, 79, 6)),
           case_z(dmod0(b, 29, 73, 2), "HIGH", "LOW"),
           concat(S("N"), dmod(b, 31, 67, 200))],
          pk_extra="  PRIMARY KEY (order_id, product_id)",
          comment="t_2nf_partial: PK (order_id, product_id); product_name/unit_price <- product_id; order_date <- order_id.")

    im1 = sub1(b)
    a3 = add(divint(im1, 20), C(1))
    b3 = add(divint(modE(im1, 20), 4), C(1))
    c3 = add(modE(im1, 4), C(1))
    table("t_partial_key3",
          [("a", "INT"), ("b", "INT"), ("c", "INT"), ("d", "VARCHAR(16)"),
           ("e", "INT"), ("f_qty", "INT"), ("f_status", "VARCHAR(8)"),
           ("f_city", "VARCHAR(12)"), ("f_flag", "CHAR(1)"),
           ("note_txt", "VARCHAR(60)")],
          200,
          [a3, b3, c3, concat(S("AB_"), a3, S("_"), b3),
           dmod(b, 13, 97, 90), dmod(b, 17, 89, 5),
           concat(S("ST_"), dmod(b, 19, 83, 4)), city_e(b, 23, 79),
           case_z(dmod0(b, 29, 73, 2)), note_e(b, 31, 67)],
          pk_extra="  PRIMARY KEY (a, b, c)",
          comment="t_partial_key3: PK (a,b,c); d depends on (a,b) only.")

    # ---- 3NF defects ----
    cid = cyc(b, 7, 40)
    table("t_3nf_transitive",
          [("order_id", "INT PRIMARY KEY"), ("customer_id", "INT"),
           ("customer_name", "VARCHAR(20)"), ("customer_email", "VARCHAR(40)"),
           ("order_date", "DATE"), ("qty", "INT"),
           ("unit_price", "DECIMAL(10,2)"), ("status", "VARCHAR(8)"),
           ("ship_city", "VARCHAR(12)"), ("pay_flag", "CHAR(1)"),
           ("channel", "VARCHAR(8)"), ("note_txt", "VARCHAR(60)")],
          300,
          [b, cid, cust_name_e(cid), cust_email_e(cid),
           dateoff("2024-01-05", cyc0(b, 23, 79)), dmod(b, 13, 97, 5),
           add(add(dmod(b, 29, 73, 40), C(10)), C(0.99)),
           status_e(), city_e(), case_z(dmod0(b, 31, 67, 2)),
           concat(S("CH_"), dmod(b, 37, 71, 3)), note_e(b, 41, 59)],
          comment="t_3nf_transitive: customer_name/email depend on customer_id.")

    dept = cyc(b, 7, 12)
    cty = cyc(b, 11, 15)
    table("t_3nf_multi",
          [("emp_id", "INT PRIMARY KEY"), ("emp_name", "VARCHAR(20)"),
           ("dept_id", "INT"), ("dept_name", "VARCHAR(20)"),
           ("dept_floor", "INT"), ("city_id", "INT"),
           ("city_name", "VARCHAR(20)"), ("city_pop", "INT"),
           ("hire_date", "DATE"), ("salary", "INT"), ("status", "VARCHAR(8)"),
           ("note_txt", "VARCHAR(60)")],
          300,
          [b, concat(S("Emp_"), lpad(b, 4)), dept,
           concat(S("Dept_"), lpad(dept, 2)),
           add(modE(mul(dept, C(7)), 5), C(1)), cty,
           concat(S("City_"), lpad(cty, 2)),
           add(modE(mul(cty, C(13)), 900), C(100)),
           dateoff("2020-01-01", cyc0(b, 23, 79)),
           mul(add(dmod(b, 29, 73, 50), C(30)), C(1000)),
           concat(S("ST_"), dmod(b, 17, 89, 3)), note_e(b, 31, 67)],
          comment="t_3nf_multi: two separate transitive FDs (dept_id, city_id).")

    wh = cyc(b, 7, 6)
    ai = cyc(b, 11, 10)
    bn = cyc(b, 13, 8)
    table("t_3nf_wide",
          [("loc_id", "INT PRIMARY KEY"), ("wh_id", "INT"), ("aisle", "INT"),
           ("bin", "INT"), ("supervisor", "VARCHAR(20)"),
           ("item_count", "INT"), ("last_check", "DATE"), ("zone", "VARCHAR(8)"),
           ("status", "VARCHAR(8)"), ("note_txt", "VARCHAR(60)")],
          1500,
          [b, wh, ai, bn,
           concat(S("SUP_"), wh, S("_"), ai, S("_"), lpad(bn, 2)),
           dmod(b, 29, 73, 500),
           dateoff("2024-03-01", cyc0(b, 23, 79)),
           concat(S("Z"), dmod(b, 19, 83, 6)), status_e(),
           note_e(b, 31, 67)],
          comment="t_3nf_wide: 1500 rows; supervisor <- (wh_id, aisle, bin).")

    table("customers_ref",
          [("customer_id", "INT PRIMARY KEY"),
           ("customer_name", "VARCHAR(20)"),
           ("customer_email", "VARCHAR(40)"), ("city", "VARCHAR(12)"),
           ("phone", "VARCHAR(16)"), ("signup_date", "DATE"),
           ("status", "VARCHAR(8)"), ("note_txt", "VARCHAR(60)")],
          150,
          [b, concat(S("Customer_"), lpad(b, 3)),
           concat(S("c"), lpad(b, 3), S("@example.com")), city_e(),
           concat(S("555-"), lpad(add(dmod(b, 23, 79, 9000), C(1000)), 4)),
           dateoff("2022-01-01", cyc0(b, 29, 73)), status_e(),
           note_e(b, 31, 67)],
          comment="customers_ref: lookup for t_cross_copy FK.")

    ccid = cyc(b, 7, 60)
    table("t_cross_copy",
          [("order_id", "INT PRIMARY KEY"), ("customer_id", "INT"),
           ("customer_name", "VARCHAR(20)"), ("order_date", "DATE"),
           ("qty", "INT"), ("unit_price", "DECIMAL(10,2)"),
           ("status", "VARCHAR(8)"), ("ship_city", "VARCHAR(12)"),
           ("flag", "CHAR(1)"), ("note_txt", "VARCHAR(60)")],
          250,
          [b, ccid,
           case_in([37, 91, 148, 203], S("Customer_999"), cust_name_e(ccid)),
           dateoff("2024-01-05", cyc0(b, 23, 79)), dmod(b, 13, 97, 5),
           add(add(dmod(b, 29, 73, 40), C(10)), C(0.99)),
           status_e(), city_e(), case_z(dmod0(b, 31, 67, 2)),
           note_e(b, 37, 71)],
          pk_extra="  CONSTRAINT fk_cross_customer FOREIGN KEY (customer_id)\n    REFERENCES customers_ref (customer_id)",
          comment="t_cross_copy: declared FK; name is a stored copy (4 dirty: 98.4% match).")

    dept10 = cyc(b, 7, 10)
    table("t_nokey_inferred",
          [("badge_no", "INT"), ("emp_name", "VARCHAR(20)"),
           ("dept_id", "INT"), ("dept_name", "VARCHAR(20)"),
           ("hire_date", "DATE"), ("salary", "INT"), ("city", "VARCHAR(12)"),
           ("status", "VARCHAR(8)"), ("lvl", "INT"),
           ("note_txt", "VARCHAR(60)")],
          200,
          [add(b, C(1000)), concat(S("Emp_"), lpad(dmod(b, 13, 97, 200), 3)),
           dept10, concat(S("Dept_"), lpad(dept10, 2)),
           dateoff("2021-01-01", cyc0(b, 23, 79)),
           mul(add(dmod(b, 29, 73, 50), C(30)), C(1000)),
           city_e(), status_e(), dmod(b, 31, 67, 5), note_e(b, 37, 71)],
          comment="t_nokey_inferred: no declared key; badge_no unique in data.")

    # ---- Must stay clean ----
    table("cc_customers",
          [("customer_id", "INT PRIMARY KEY"),
           ("customer_name", "VARCHAR(20)"),
           ("customer_email", "VARCHAR(40)"), ("city", "VARCHAR(12)"),
           ("phone", "VARCHAR(16)"), ("signup_date", "DATE"),
           ("status", "VARCHAR(8)"), ("note_txt", "VARCHAR(60)")],
          40,
          [b, concat(S("Customer_"), lpad(b, 3)),
           concat(S("c"), lpad(b, 3), S("@example.com")), city_e(),
           concat(S("555-"), lpad(add(dmod(b, 23, 79, 9000), C(1000)), 4)),
           dateoff("2022-01-01", cyc0(b, 29, 73)), status_e(),
           note_e(b, 31, 67)],
          comment="cc_customers: normalized reference.")

    table("cc_products",
          [("product_id", "INT PRIMARY KEY"),
           ("product_name", "VARCHAR(20)"), ("category", "VARCHAR(8)"),
           ("unit_price", "DECIMAL(10,2)"), ("weight", "INT"),
           ("status", "VARCHAR(8)"), ("note_txt", "VARCHAR(60)")],
          60,
          [b, concat(S("Product_"), lpad(b, 3)),
           concat(S("CAT_"), add(modE(mul(cyc(b, 11, 60), C(7)), 6), C(1))),
           price_of(cyc(b, 7, 60)), dmod(b, 13, 97, 900), status_e(),
           note_e(b, 19, 83)],
          comment="cc_products: normalized reference.")

    table("cc_orders",
          [("order_id", "INT PRIMARY KEY"), ("customer_id", "INT"),
           ("order_date", "DATE"), ("status", "VARCHAR(8)"),
           ("ship_city", "VARCHAR(12)"), ("total_amt", "DECIMAL(10,2)"),
           ("flag", "CHAR(1)"), ("note_txt", "VARCHAR(60)")],
          200,
          [b, cyc(b, 7, 40), dateoff("2024-01-05", cyc0(b, 23, 79)),
           status_e(), city_e(),
           add(add(dmod(b, 29, 73, 900), C(100)), C(0.99)),
           case_z(dmod0(b, 31, 67, 2)), note_e(b, 37, 71)],
          pk_extra="  CONSTRAINT fk_cco_customer FOREIGN KEY (customer_id)\n    REFERENCES cc_customers (customer_id)",
          comment="cc_orders: normalized; FK to cc_customers.")

    table("cc_order_lines",
          [("order_id", "INT"), ("product_id", "INT"), ("qty", "INT"),
           ("price_charged", "DECIMAL(10,2)"), ("discount", "INT"),
           ("ship_mode", "VARCHAR(8)"), ("flag", "CHAR(1)"),
           ("note_txt", "VARCHAR(60)")],
          300,
          [cyc(b, 7, 200), cyc(b, 11, 60), dmod(b, 13, 97, 5),
           add(add(dmod(b, 29, 73, 40), C(10)), C(0.99)),
           dmod0(b, 19, 83, 10), concat(S("SM_"), dmod(b, 23, 79, 4)),
           case_z(dmod0(b, 31, 67, 2)), note_e(b, 37, 71)],
          pk_extra="  PRIMARY KEY (order_id, product_id),\n"
                   "  CONSTRAINT fk_ccol_order FOREIGN KEY (order_id) REFERENCES cc_orders (order_id),\n"
                   "  CONSTRAINT fk_ccol_product FOREIGN KEY (product_id) REFERENCES cc_products (product_id)",
          comment="cc_order_lines: normalized; composite key, full-key deps only.")

    st_b = add(divint(im1, 3), C(1))
    su_b = add(modE(im1, 3), C(1))
    table("t_bcnf_only",
          [("student_id", "INT"), ("subject_code", "INT"),
           ("teacher_name", "VARCHAR(12)"), ("grade", "CHAR(1)"),
           ("semester", "VARCHAR(8)"), ("note_txt", "VARCHAR(60)")],
          210,
          [st_b, su_b,
           concat(S("T"), lpad(sub(mul(su_b, C(2)), modE(b, 2)), 2)),
           case_grade(cyc0(b, 13, 97)),
           concat(S("SEM_"), dmod(b, 17, 89, 4)),
           concat(S("N"), dmod(b, 19, 83, 200))],
          pk_extra="  PRIMARY KEY (student_id, subject_code)",
          comment="t_bcnf_only: teacher->subject (2 teachers/subject); 3NF but not BCNF.")

    table("t_lookup_codes",
          [("code", "VARCHAR(8) PRIMARY KEY"), ("description", "VARCHAR(40)"),
           ("category", "VARCHAR(8)"), ("status", "VARCHAR(8)"),
           ("note_txt", "VARCHAR(60)")],
          50,
          [concat(S("C"), lpad(b, 3)), concat(S("Desc_"), lpad(b, 3)),
           concat(S("G"), cyc(b, 7, 5)), status_e(),
           concat(S("N"), dmod(b, 19, 83, 200))],
          comment="t_lookup_codes: code is the key.")

    a2 = add(divint(im1, 20), C(1))
    b2 = add(modE(im1, 20), C(1))
    table("t_composite_ok",
          [("a", "INT"), ("b", "INT"), ("c", "VARCHAR(16)"),
           ("d", "VARCHAR(16)"), ("e", "INT"), ("f_status", "VARCHAR(8)"),
           ("note_txt", "VARCHAR(60)")],
          200,
          [a2, b2, concat(S("C_"), a2, S("_"), b2),
           concat(S("D_"), b2, S("_"), a2), dmod(b, 13, 97, 90),
           status_e(), concat(S("N"), dmod(b, 19, 83, 200))],
          pk_extra="  PRIMARY KEY (a, b)",
          comment="t_composite_ok: PK (a,b); c,d depend on the whole key.")

    # ---- Decoys ----
    table("d_unique_everything",
          [("id", "INT PRIMARY KEY"), ("u1", "INT"), ("u2", "INT"),
           ("code", "VARCHAR(12)"), ("email", "VARCHAR(40)"),
           ("token", "CHAR(32)"), ("note_txt", "VARCHAR(20)"),
           ("flag", "CHAR(1)")],
          50,
          [b, add(b, C(100000)), sub(C(200000), b),
           concat(S("U-"), lpad(add(b, C(5000)), 6)),
           concat(S("user"), b, S("@example.com")), md5iv(),
           concat(S("N"), b), case_parity(S("Y"), S("N"))],
          comment="d_unique_everything: every value unique.")

    table("d_coincidence",
          [("id", "INT PRIMARY KEY"), ("cat_a", "VARCHAR(8)"),
           ("cat_b", "VARCHAR(8)"), ("cat_c", "VARCHAR(8)"),
           ("cat_d", "VARCHAR(8)"), ("cat_e", "VARCHAR(8)"),
           ("cat_f", "VARCHAR(8)"), ("cat_g", "VARCHAR(8)"), ("qty", "INT"),
           ("note_txt", "VARCHAR(60)")],
          100,
          [b, concat(S("A"), dmod(b, 7, 97, 9)),
           concat(S("B"), dmod(b, 11, 89, 7)),
           concat(S("C"), dmod(b, 13, 83, 11)),
           concat(S("D"), dmod(b, 17, 79, 13)),
           case_lt(dmod0(b, 19, 73, 73), 55, S("E1"),
                   case_lt(dmod0(b, 19, 73, 73), 68, S("E2"), S("E3"))),
           case_lt(dmod0(b, 23, 71, 71), 60, S("F1"), S("F2")),
           concat(S("G"), dmod(b, 29, 67, 5)),
           dmod(b, 31, 61, 5), note_e(b, 37, 59)],
          comment="d_coincidence: independent skewed categoricals.")

    table("d_many_columns",
          [("id", "INT PRIMARY KEY"), ("c01", "VARCHAR(8)"),
           ("c02", "VARCHAR(8)"), ("c03", "VARCHAR(8)"),
           ("c04", "VARCHAR(8)"), ("c05", "VARCHAR(8)"),
           ("c06", "VARCHAR(8)"), ("c07", "VARCHAR(8)"),
           ("c08", "VARCHAR(8)"), ("c09", "VARCHAR(8)"),
           ("c10", "VARCHAR(8)"), ("c11", "VARCHAR(8)"),
           ("c12", "VARCHAR(8)"), ("c13", "VARCHAR(8)"),
           ("c14", "VARCHAR(8)")],
          1000,
          [b, concat(S("a"), dmod(b, 7, 97, 7)),
           concat(S("b"), dmod(b, 11, 89, 9)),
           concat(S("c"), dmod(b, 13, 83, 11)),
           concat(S("d"), dmod(b, 17, 79, 13)),
           concat(S("e"), dmod(b, 19, 73, 17)),
           concat(S("f"), dmod(b, 23, 71, 19)),
           concat(S("g"), dmod(b, 29, 67, 23)),
           concat(S("h"), dmod(b, 31, 61, 29)),
           concat(S("i"), dmod(b, 37, 59, 31)),
           concat(S("j"), dmod(b, 41, 53, 37)),
           concat(S("k"), dmod(b, 43, 47, 41)),
           concat(S("l"), dmod(b, 47, 43, 43)),
           case_lt(dmod0(b, 53, 41, 41), 30, S("m1"), S("m2")),
           concat(S("n"), dmod(b, 59, 37, 5))],
          comment="d_many_columns: 15 independent categoricals x 1000 rows.")

    table("d_nulls",
          [("id", "INT PRIMARY KEY"), ("c1", "VARCHAR(12)"),
           ("c2", "VARCHAR(12)"), ("c3", "VARCHAR(12)"), ("n1", "INT"),
           ("note_txt", "VARCHAR(60)"), ("flag", "CHAR(1)"), ("qty", "INT")],
          150,
          [b, case_null(10, 3, concat(S("A"), cyc(b, 7, 40))),
           case_null(10, 4, concat(S("B"), cyc(b, 11, 35))),
           case_null(10, 2, concat(S("C"), cyc(b, 13, 28))),
           case_null(10, 3, add(cyc0(b, 17, 89), C(1))),
           case_null(10, 5, concat(S("N"), dmod(b, 19, 83, 200))),
           case_z(dmod0(b, 23, 79, 2)), dmod(b, 29, 73, 5)],
          comment="d_nulls: heavy NULLs, independent columns.")

    dept25 = cyc(b, 7, 25)
    table("d_near_fd",
          [("id", "INT PRIMARY KEY"), ("dept_id", "INT"),
           ("dept_name", "VARCHAR(20)"), ("hire_date", "DATE"),
           ("salary", "INT"), ("city", "VARCHAR(12)"),
           ("status", "VARCHAR(8)"), ("note_txt", "VARCHAR(60)")],
          200,
          [b, dept25,
           case_in([40, 121], S("Dept_XX_WRONG"),
                   concat(S("Dept_"), lpad(dept25, 2))),
           dateoff("2021-01-01", cyc0(b, 23, 79)),
           mul(add(dmod(b, 29, 73, 50), C(30)), C(1000)),
           city_e(), status_e(), note_e(b, 31, 67)],
          comment="d_near_fd: dept_id->dept_name with 2 dirty rows (1%).")

    table("d_dirty_pairs",
          [("id", "INT PRIMARY KEY"), ("pair_id", "INT"),
           ("side", "VARCHAR(8)"), ("note_txt", "VARCHAR(60)"),
           ("flag", "CHAR(1)"), ("amt", "INT")],
          120,
          [b, add(divint(im1, 2), C(1)),
           case_parity(S("LEFT"), S("RIGHT")),
           concat(S("N"), dmod(b, 13, 97, 200)),
           case_z(dmod0(b, 17, 89, 2)), dmod(b, 19, 83, 500)],
          comment="d_dirty_pairs: every group has 2 disagreeing rows.")

    table("d_equiv_cols",
          [("code", "VARCHAR(8) PRIMARY KEY"), ("country", "VARCHAR(20)"),
           ("capital", "VARCHAR(20)"), ("currency", "VARCHAR(12)"),
           ("note_txt", "VARCHAR(60)")],
          10,
          [concat(S("CC"), lpad(b, 2)),
           concat(S("Country_CC"), lpad(b, 2)),
           concat(S("Capital_CC"), lpad(b, 2)),
           concat(S("Cur_CC"), lpad(b, 2)), concat(S("N"), b)],
          comment="d_equiv_cols: 10-row lookup; code <-> country 1-1.")

    table("d_wide_text",
          [("id", "INT PRIMARY KEY"), ("title", "VARCHAR(120)"),
           ("body", "MEDIUMTEXT"), ("attachment", "BLOB"), ("meta", "JSON"),
           ("created_date", "DATE"), ("author", "VARCHAR(20)")],
          80,
          [b, concat(S("Doc_"), lpad(b, 4), S("_"), S("x" * 20)),
           repeat_lorem(), repeat_bin(), json_k(),
           dateoff("2023-01-01", cyc0(b, 23, 79)),
           concat(S("Author_"), dmod(b, 29, 73, 10))],
          comment="d_wide_text: LOB/JSON excluded from FD search.")

    table("d_name_trap",
          [("id", "INT PRIMARY KEY"), ("user_id", "INT"),
           ("user_name", "VARCHAR(20)"), ("signup_date", "DATE"),
           ("status", "VARCHAR(8)"), ("city", "VARCHAR(12)"),
           ("logins", "INT"), ("note_txt", "VARCHAR(60)")],
          150,
          [b, cyc(b, 7, 50), concat(S("User_"), lpad(dmod(b, 13, 97, 97), 3)),
           dateoff("2023-01-01", cyc0(b, 23, 79)), status_e(), city_e(),
           dmod(b, 29, 73, 500), note_e(b, 31, 67)],
          comment="d_name_trap: naming suggests user_id->user_name; data disagrees.")

    cid100 = cyc(b, 7, 100)
    table("t_big_sample",
          [("id", "INT PRIMARY KEY"), ("customer_id", "INT"),
           ("customer_name", "VARCHAR(20)"), ("order_date", "DATE"),
           ("qty", "INT"), ("unit_price", "DECIMAL(10,2)"),
           ("status", "VARCHAR(8)"), ("ship_city", "VARCHAR(12)"),
           ("flag", "CHAR(1)"), ("note_txt", "VARCHAR(60)")],
          55000,
          [b, cid100, cust_name_e(cid100),
           dateoff("2024-01-05", cyc0(b, 23, 79)), dmod(b, 13, 97, 5),
           add(add(dmod(b, 29, 73, 40), C(10)), C(0.99)),
           status_e(), city_e(), case_z(dmod0(b, 31, 67, 2)),
           note_e(b, 37, 71)],
          comment="t_big_sample: 55000 rows (> MAX_ROWS_PER_TABLE); transitive FD.")


PG_TYPEMAP = {"MEDIUMTEXT": "TEXT", "BLOB": "BYTEA"}


def ddl(table, d):
    lines = []
    if table["comment"]:
        lines.append("-- " + table["comment"])
    cols = []
    for name, typ in table["cols"]:
        if d == "pg":
            typ = PG_TYPEMAP.get(typ, typ)
        cols.append("  " + name + " " + typ)
    if table["pk_extra"]:
        cols.append(table["pk_extra"])
    engine = " ENGINE=InnoDB" if d == "my" else ""
    lines.append("CREATE TABLE " + table["name"] + " (\n" + ",\n".join(cols) + "\n)" + engine + ";")
    return "\n".join(lines)


def insert_sql(table, d):
    n = table["n"]
    exprs = table["exprs"]
    sel = ",\n  ".join(e.sql(d, "j" if table.get("from_hook") == "jblock" else ("i" if d == "my" else "s.i")) for e in exprs)
    if table.get("from_hook") == "jblock":
        if d == "my":
            frm = "(SELECT ((i-1)%60)+1 AS j FROM seq WHERE i BETWEEN 1 AND " + str(n) + ") t"
        else:
            frm = "(SELECT ((s.i-1)%60)+1 AS j FROM generate_series(1," + str(n) + ") AS s(i)) t"
    elif d == "my":
        frm = "seq WHERE i BETWEEN 1 AND " + str(n)
    else:
        frm = "generate_series(1," + str(n) + ") AS s(i)"
    return "INSERT INTO " + table["name"] + "\nSELECT\n  " + sel + "\nFROM " + frm + ";"


D_TINY = (
    "-- d_tiny: 5 rows where a->b happens to hold -> insufficient_data.\n"
    "CREATE TABLE d_tiny (\n"
    "  a INT, b VARCHAR(8), c INT, d VARCHAR(8), e INT, f_note VARCHAR(20)\n"
    ");\n"
    "INSERT INTO d_tiny VALUES\n"
    "  (1,'x',10,'p',100,'n1'), (1,'x',20,'q',101,'n2'),\n"
    "  (2,'y',10,'p',102,'n3'), (2,'y',30,'r',103,'n4'),\n"
    "  (3,'z',10,'p',104,'n5');"
)


def render_mysql():
    L = []
    L.append("-- " + "=" * 67)
    L.append("-- DB Normalization Checker: planted-defect test database (MySQL 8.0.41)")
    L.append("-- " + "=" * 67)
    L.append("-- Load with:")
    L.append("--   mysql -h localhost -u root -p < scripts/testdb/mysql_planted.sql")
    L.append("-- Deterministic: reloading reproduces byte-identical tables.")
    L.append("-- Expected verdicts: scripts/testdb/expected_verdicts.json")
    L.append("-- Generated by scripts/testdb/render_planted.py -- do not hand-edit.")
    L.append("DROP DATABASE IF EXISTS normtest;")
    L.append("CREATE DATABASE normtest CHARACTER SET utf8mb4;")
    L.append("USE normtest;")
    L.append("")
    L.append("-- Row-number helper 1..55000 (doubling, no procedures needed).")
    L.append("CREATE TABLE seq (i INT PRIMARY KEY) ENGINE=InnoDB;")
    L.append("INSERT INTO seq VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9);")
    cur = 10
    while cur <= 55000:
        L.append("INSERT INTO seq SELECT i+%d FROM seq;" % cur)
        cur *= 2
    L.append("DELETE FROM seq WHERE i = 0 OR i > 55000;")
    L.append("")
    for t in TABLES:
        L.append(ddl(t, "my"))
        L.append(insert_sql(t, "my"))
        L.append("")
    L.append(D_TINY)
    L.append("")
    L.append("DROP TABLE seq;")
    L.append("")
    return "\n".join(L)


def render_pg():
    L = []
    L.append("-- " + "=" * 67)
    L.append("-- DB Normalization Checker: planted-defect test database (PostgreSQL 17.4)")
    L.append("-- " + "=" * 67)
    L.append("-- Load with:")
    L.append("--   psql -h localhost -U postgres -f scripts/testdb/postgres_planted.sql")
    L.append("-- Same logical schema and row-for-row identical data as mysql_planted.sql.")
    L.append("-- Expected verdicts: scripts/testdb/expected_verdicts.json")
    L.append("-- Generated by scripts/testdb/render_planted.py -- do not hand-edit.")
    L.append("DROP DATABASE IF EXISTS normtest;")
    L.append("CREATE DATABASE normtest;")
    L.append("\\c normtest")
    L.append("")
    for t in TABLES:
        L.append(ddl(t, "pg"))
        L.append(insert_sql(t, "pg"))
        L.append("")
    L.append(D_TINY)
    L.append("")
    return "\n".join(L)


def main():
    define_tables()
    (OUT / "mysql_planted.sql").write_text(render_mysql(), encoding="utf-8")
    (OUT / "postgres_planted.sql").write_text(render_pg(), encoding="utf-8")
    total = sum(t["n"] for t in TABLES)
    print("tables: %d  total rows: %d" % (len(TABLES), total))
    for t in TABLES:
        print("  %-22s %6d" % (t["name"], t["n"]))


if __name__ == "__main__":
    main()
