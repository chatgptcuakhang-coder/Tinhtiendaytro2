import os
import re
import smtplib
import sqlite3
import ssl
from contextlib import closing
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

import pandas as pd
import streamlit as st
from fpdf import FPDF

# ======================================
# CẤU HÌNH CHUNG
# ======================================
DB_PATH = "tien_tro.db"
TEN_DAY_TRO = "DÃY TRỌ VŨ ĐỨC BÌNH"
TRANG_THAI = ["Chưa thanh toán", "Đã thanh toán"]
GIA_PHONG_MAC_DINH = 2_500_000
CAI_DAT_MAC_DINH = {
    "gia_dien": 3500,
    "gia_nuoc": 15000,
    "phi_khac": 150000,
    "so_phong": 10,
}

st.set_page_config(
    page_title="Quản lý tiền phòng trọ",
    layout="wide",
)


# ======================================
# DATABASE (SQLITE)
# ======================================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(get_conn()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS month_prices (
                nam      INTEGER NOT NULL,
                thang    INTEGER NOT NULL,
                gia_dien INTEGER NOT NULL,
                gia_nuoc INTEGER NOT NULL,
                phi_khac INTEGER NOT NULL,
                PRIMARY KEY (nam, thang)
            );

            CREATE TABLE IF NOT EXISTS records (
                nam        INTEGER NOT NULL,
                thang      INTEGER NOT NULL,
                phong      INTEGER NOT NULL,
                gia_phong  INTEGER NOT NULL DEFAULT 0,
                nguoi_o    TEXT    NOT NULL DEFAULT '',
                sdt        TEXT    NOT NULL DEFAULT '',
                email      TEXT    NOT NULL DEFAULT '',
                so_nguoi   INTEGER NOT NULL DEFAULT 1,
                dien_cu    INTEGER NOT NULL DEFAULT 0,
                dien_moi   INTEGER NOT NULL DEFAULT 0,
                nuoc_cu    INTEGER NOT NULL DEFAULT 0,
                nuoc_moi   INTEGER NOT NULL DEFAULT 0,
                trang_thai TEXT    NOT NULL DEFAULT 'Chưa thanh toán',
                PRIMARY KEY (nam, thang, phong)
            );
            """
        )
        # Nâng cấp database cũ: thêm cột email nếu chưa có
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(records)").fetchall()]
        if "email" not in cols:
            conn.execute("ALTER TABLE records ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        conn.commit()


def get_setting(key):
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return int(row["value"]) if row else CAI_DAT_MAC_DINH[key]


def set_setting(key, value):
    with closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, int(value)),
        )
        conn.commit()


def get_month_prices(nam, thang):
    """Đơn giá của từng tháng. Tháng mới sẽ kế thừa đơn giá của tháng gần nhất trước đó."""
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT gia_dien, gia_nuoc, phi_khac FROM month_prices WHERE nam = ? AND thang = ?",
            (nam, thang),
        ).fetchone()
        if row:
            return dict(row)

        row = conn.execute(
            "SELECT gia_dien, gia_nuoc, phi_khac FROM month_prices "
            "WHERE (nam * 100 + thang) < ? ORDER BY (nam * 100 + thang) DESC LIMIT 1",
            (nam * 100 + thang,),
        ).fetchone()
        prices = dict(row) if row else {
            k: CAI_DAT_MAC_DINH[k] for k in ("gia_dien", "gia_nuoc", "phi_khac")
        }
        conn.execute(
            "INSERT INTO month_prices (nam, thang, gia_dien, gia_nuoc, phi_khac) VALUES (?, ?, ?, ?, ?)",
            (nam, thang, prices["gia_dien"], prices["gia_nuoc"], prices["phi_khac"]),
        )
        conn.commit()
    return prices


def save_month_prices(nam, thang, gia_dien, gia_nuoc, phi_khac, ap_dung_thang_sau=False):
    with closing(get_conn()) as conn:
        if ap_dung_thang_sau:
            # Áp dụng cho tháng này và tất cả các tháng sau đã có đơn giá
            conn.execute(
                "UPDATE month_prices SET gia_dien = ?, gia_nuoc = ?, phi_khac = ? "
                "WHERE (nam * 100 + thang) >= ?",
                (gia_dien, gia_nuoc, phi_khac, nam * 100 + thang),
            )
        else:
            conn.execute(
                "UPDATE month_prices SET gia_dien = ?, gia_nuoc = ?, phi_khac = ? "
                "WHERE nam = ? AND thang = ?",
                (gia_dien, gia_nuoc, phi_khac, nam, thang),
            )
        conn.commit()


def ensure_records(nam, thang, so_phong):
    """Tạo dữ liệu cho các phòng còn thiếu, kế thừa từ lần ghi gần nhất của phòng đó."""
    with closing(get_conn()) as conn:
        for phong in range(1, so_phong + 1):
            exists = conn.execute(
                "SELECT 1 FROM records WHERE nam = ? AND thang = ? AND phong = ?",
                (nam, thang, phong),
            ).fetchone()
            if exists:
                continue

            prev = conn.execute(
                "SELECT * FROM records WHERE phong = ? AND (nam * 100 + thang) < ? "
                "ORDER BY (nam * 100 + thang) DESC LIMIT 1",
                (phong, nam * 100 + thang),
            ).fetchone()

            if prev:
                values = (
                    nam, thang, phong, prev["gia_phong"], prev["nguoi_o"], prev["sdt"],
                    prev["email"], prev["so_nguoi"], prev["dien_moi"], prev["dien_moi"],
                    prev["nuoc_moi"], prev["nuoc_moi"], TRANG_THAI[0],
                )
            else:
                values = (
                    nam, thang, phong, GIA_PHONG_MAC_DINH, "", "", "", 1, 0, 0, 0, 0, TRANG_THAI[0],
                )
            conn.execute(
                "INSERT INTO records (nam, thang, phong, gia_phong, nguoi_o, sdt, email, so_nguoi, "
                "dien_cu, dien_moi, nuoc_cu, nuoc_moi, trang_thai) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )
        conn.commit()


def get_record(nam, thang, phong):
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM records WHERE nam = ? AND thang = ? AND phong = ?",
            (nam, thang, phong),
        ).fetchone()
    return dict(row)


def get_records(nam, thang, so_phong):
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM records WHERE nam = ? AND thang = ? AND phong <= ? ORDER BY phong",
            (nam, thang, so_phong),
        ).fetchall()
    return [dict(r) for r in rows]


def save_record(nam, thang, phong, d):
    with closing(get_conn()) as conn:
        conn.execute(
            "UPDATE records SET gia_phong = ?, nguoi_o = ?, sdt = ?, email = ?, so_nguoi = ?, "
            "dien_cu = ?, dien_moi = ?, nuoc_cu = ?, nuoc_moi = ?, trang_thai = ? "
            "WHERE nam = ? AND thang = ? AND phong = ?",
            (
                d["gia_phong"], d["nguoi_o"], d["sdt"], d["email"], d["so_nguoi"],
                d["dien_cu"], d["dien_moi"], d["nuoc_cu"], d["nuoc_moi"], d["trang_thai"],
                nam, thang, phong,
            ),
        )
        # Cập nhật số cũ cho tháng sau (nếu tháng sau đã có dữ liệu)
        if thang == 12:
            nam_sau, thang_sau = nam + 1, 1
        else:
            nam_sau, thang_sau = nam, thang + 1
        conn.execute(
            "UPDATE records SET dien_cu = ?, nuoc_cu = ? WHERE nam = ? AND thang = ? AND phong = ?",
            (d["dien_moi"], d["nuoc_moi"], nam_sau, thang_sau, phong),
        )
        conn.commit()


# ======================================
# TÍNH TOÁN
# ======================================
def tinh_tien(rec, prices):
    so_dien = max(rec["dien_moi"] - rec["dien_cu"], 0)
    so_nuoc = max(rec["nuoc_moi"] - rec["nuoc_cu"], 0)
    tien_dien = so_dien * prices["gia_dien"]
    tien_nuoc = so_nuoc * prices["gia_nuoc"]
    tong = rec["gia_phong"] + tien_dien + tien_nuoc + prices["phi_khac"]
    return {
        "so_dien": so_dien,
        "so_nuoc": so_nuoc,
        "tien_dien": tien_dien,
        "tien_nuoc": tien_nuoc,
        "phi_khac": prices["phi_khac"],
        "tong": tong,
    }


def tien(x):
    return f"{int(x):,}".replace(",", ".")


# ======================================
# XUẤT PDF
# ======================================
FONT_REGULAR = [
    "fonts/DejaVuSans.ttf",
    "fonts/Roboto-Regular.ttf",
    "fonts/NotoSans-Regular.ttf",
    "fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
FONT_BOLD = [
    "fonts/DejaVuSans-Bold.ttf",
    "fonts/Roboto-Bold.ttf",
    "fonts/NotoSans-Bold.ttf",
    "fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def tim_font(danh_sach):
    for path in danh_sach:
        if os.path.exists(path):
            return path
    return None


def tao_pdf(nam, thang, danh_sach):
    """danh_sach: list các (record, kết quả tính tiền, đơn giá). Mỗi phòng một trang."""
    reg = tim_font(FONT_REGULAR)
    if not reg:
        raise FileNotFoundError(
            "Không tìm thấy font hỗ trợ tiếng Việt. Hãy copy file DejaVuSans.ttf "
            "(và DejaVuSans-Bold.ttf) vào thư mục 'fonts' cạnh file app.py."
        )
    bold = tim_font(FONT_BOLD) or reg

    pdf = FPDF(orientation="P", unit="mm", format="A5")
    pdf.set_auto_page_break(auto=False)
    pdf.add_font("VN", "", reg)
    pdf.add_font("VN", "B", bold)

    w = [30, 64, 34]  # tổng 128mm (A5 trừ lề 10mm hai bên)

    for rec, kq, prices in danh_sach:
        pdf.add_page()
        pdf.set_margins(10, 10, 10)
        pdf.set_xy(10, 12)

        pdf.set_font("VN", "B", 13)
        pdf.cell(0, 7, TEN_DAY_TRO, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("VN", "B", 16)
        pdf.cell(0, 9, "PHIẾU THU TIỀN PHÒNG", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("VN", "", 10)
        pdf.cell(0, 6, f"Tháng {thang}/{nam}", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        # Thông tin phòng và người ở
        thong_tin = [
            ("Phòng:", str(rec["phong"])),
            ("Người ở:", rec["nguoi_o"] or "-"),
            ("Điện thoại:", rec["sdt"] or "-"),
            ("Số người ở:", str(rec["so_nguoi"])),
        ]
        for nhan, gia_tri in thong_tin:
            pdf.set_font("VN", "B", 10)
            pdf.cell(30, 6, nhan)
            pdf.set_font("VN", "", 10)
            pdf.cell(0, 6, gia_tri, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        # Bảng chi tiết
        pdf.set_font("VN", "B", 10)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(w[0], 8, "Khoản mục", border=1, align="C", fill=True)
        pdf.cell(w[1], 8, "Chi tiết", border=1, align="C", fill=True)
        pdf.cell(w[2], 8, "Thành tiền (đ)", border=1, align="C", fill=True,
                 new_x="LMARGIN", new_y="NEXT")

        dong = [
            ("Tiền phòng", "", rec["gia_phong"]),
            (
                "Tiền điện",
                f"{rec['dien_cu']} → {rec['dien_moi']} = {kq['so_dien']} kWh x {tien(prices['gia_dien'])}",
                kq["tien_dien"],
            ),
            (
                "Tiền nước",
                f"{rec['nuoc_cu']} → {rec['nuoc_moi']} = {kq['so_nuoc']} m³ x {tien(prices['gia_nuoc'])}",
                kq["tien_nuoc"],
            ),
            ("Phí khác", "Wifi, vệ sinh...", kq["phi_khac"]),
        ]
        pdf.set_font("VN", "", 9)
        for ten, chi_tiet, so_tien in dong:
            pdf.cell(w[0], 8, ten, border=1)
            pdf.cell(w[1], 8, chi_tiet, border=1)
            pdf.cell(w[2], 8, tien(so_tien), border=1, align="R", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("VN", "B", 11)
        pdf.cell(w[0] + w[1], 9, "TỔNG CỘNG", border=1, align="C")
        pdf.cell(w[2], 9, tien(kq["tong"]), border=1, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        pdf.set_font("VN", "B", 10)
        pdf.cell(35, 6, "Trạng thái:")
        pdf.set_font("VN", "", 10)
        pdf.cell(0, 6, rec["trang_thai"], new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        pdf.set_font("VN", "", 9)
        pdf.cell(0, 5, f"Ngày lập: {datetime.now().strftime('%d/%m/%Y')}", align="R",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        # Chữ ký
        pdf.set_font("VN", "B", 10)
        pdf.cell(64, 6, "Chủ trọ", align="C")
        pdf.cell(64, 6, "Người thuê", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("VN", "", 8)
        pdf.cell(64, 5, "(Ký, ghi rõ họ tên)", align="C")
        pdf.cell(64, 5, "(Ký, ghi rõ họ tên)", align="C", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


# ======================================
# GỬI EMAIL
# ======================================
def email_hop_le(addr):
    return bool(addr) and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr.strip()) is not None


THONG_BAO_CHUA_CAU_HINH = (
    "Chưa cấu hình tài khoản gửi email. Cách 1: mở sidebar > 'Cấu hình gửi email', "
    "điền Email gửi đi và Mật khẩu ứng dụng (16 ký tự của Google). "
    "Cách 2: khai báo mục [smtp] trong Secrets (xem hướng dẫn trong file secrets.toml)."
)


def la_mat_khau_mau(pw):
    return (not pw) or str(pw).startswith("DAN_MAT_KHAU")


def lay_cau_hinh_smtp():
    """Ưu tiên st.secrets["smtp"]; nếu không có thì dùng thông tin nhập ở sidebar."""
    try:
        sec = st.secrets["smtp"]
        if sec.get("user") and not la_mat_khau_mau(sec.get("password")):
            return {
                "host": sec.get("host", "smtp.gmail.com"),
                "port": int(sec.get("port", 587)),
                "user": sec["user"],
                "password": sec["password"],
                "ten": sec.get("sender_name", TEN_DAY_TRO),
            }
    except Exception:
        pass

    user = st.session_state.get("smtp_user", "").strip()
    password = st.session_state.get("smtp_password", "")
    if user and password:
        return {
            "host": st.session_state.get("smtp_host", "smtp.gmail.com").strip(),
            "port": int(st.session_state.get("smtp_port", 587)),
            "user": user,
            "password": password,
            "ten": st.session_state.get("smtp_ten", TEN_DAY_TRO),
        }
    return None


def gui_phieu(cfg, rec, pdf_bytes, nam, thang):
    msg = EmailMessage()
    msg["Subject"] = f"Phiếu thu tiền phòng {rec['phong']} - Tháng {thang}/{nam}"
    msg["From"] = formataddr((cfg["ten"], cfg["user"]))
    msg["To"] = rec["email"].strip()
    ten = rec["nguoi_o"] or "anh/chị"
    msg.set_content(
        f"Xin chào {ten},\n\n"
        f"{TEN_DAY_TRO} gửi anh/chị phiếu thu tiền phòng {rec['phong']} "
        f"tháng {thang}/{nam} (file PDF đính kèm).\n"
        f"Nếu có thắc mắc, anh/chị vui lòng liên hệ chủ trọ.\n\nTrân trọng."
    )
    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=f"Phieu_Phong_{rec['phong']}_{thang:02d}_{nam}.pdf",
    )

    def _gui(port):
        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(cfg["host"], port, context=context, timeout=30) as srv:
                srv.login(cfg["user"], cfg["password"])
                srv.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=30) as srv:
                srv.ehlo()
                srv.starttls(context=context)
                srv.ehlo()
                srv.login(cfg["user"], cfg["password"])
                srv.send_message(msg)

    try:
        _gui(cfg["port"])
    except (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError):
        # Cổng 587 bị đóng kết nối thì thử lại bằng cổng 465 (SSL)
        if cfg["port"] == 465:
            raise
        _gui(465)


def dich_loi_smtp(ex):
    if isinstance(ex, smtplib.SMTPAuthenticationError):
        return (
            "Gmail từ chối đăng nhập. Hãy dùng Mật khẩu ứng dụng 16 ký tự (không phải mật khẩu "
            "đăng nhập Gmail) và đảm bảo Email gửi đi là tài khoản đã tạo mật khẩu ứng dụng đó."
        )
    if isinstance(ex, smtplib.SMTPServerDisconnected):
        return (
            "Máy chủ Gmail đóng kết nối (thường do sai mật khẩu/tài khoản hoặc bị chặn). "
            f"Chi tiết: {ex}"
        )
    return str(ex)


def kiem_tra_smtp(cfg):
    """Thử kết nối + đăng nhập (không gửi thư) và báo rõ lỗi ở bước nào."""
    buoc = "kết nối tới máy chủ"
    try:
        context = ssl.create_default_context()
        if cfg["port"] == 465:
            srv = smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=20)
        else:
            srv = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
            buoc = "bật mã hóa TLS"
            srv.ehlo()
            srv.starttls(context=context)
            srv.ehlo()
        buoc = "đăng nhập"
        srv.login(cfg["user"], cfg["password"])
        srv.quit()
        return True, "Kết nối và đăng nhập thành công. Có thể gửi email."
    except Exception as ex:
        return False, f"Lỗi ở bước '{buoc}' ({type(ex).__name__}): {dich_loi_smtp(ex)}"


# ======================================
# GIAO DIỆN
# ======================================
init_db()

st.title(f"PHẦN MỀM QUẢN LÝ THU TIỀN {TEN_DAY_TRO}")

# ---------- SIDEBAR ----------
st.sidebar.header("Cấu hình")

so_phong = st.sidebar.slider(
    "Số lượng phòng",
    min_value=1,
    max_value=100,
    value=get_setting("so_phong"),
    step=1,
)
if so_phong != get_setting("so_phong"):
    set_setting("so_phong", so_phong)

col_nam, col_thang = st.sidebar.columns(2)
with col_nam:
    nam_hien_tai = datetime.now().year
    danh_sach_nam = list(range(nam_hien_tai - 1, nam_hien_tai + 6))
    nam_chon = st.selectbox(
        "Chọn năm:", danh_sach_nam, index=danh_sach_nam.index(nam_hien_tai)
    )
with col_thang:
    idx_thang_chon = st.selectbox(
        "Chọn tháng:",
        list(range(1, 13)),
        index=datetime.now().month - 1,
        format_func=lambda x: f"Tháng {x}",
    )

thang_chon = f"Tháng {idx_thang_chon}/{nam_chon}"

st.sidebar.header("Đơn giá điện nước")
st.sidebar.caption(f"Đơn giá áp dụng riêng cho {thang_chon}")
prices_db = get_month_prices(nam_chon, idx_thang_chon)
suffix = f"{nam_chon}_{idx_thang_chon}"

with st.sidebar.form(key=f"form_gia_{suffix}"):
    f_gia_dien = st.number_input(
        "Giá điện (đ/kWh)", min_value=0, value=int(prices_db["gia_dien"]), step=100,
    )
    f_gia_nuoc = st.number_input(
        "Giá nước (đ/m³)", min_value=0, value=int(prices_db["gia_nuoc"]), step=500,
    )
    f_phi_khac = st.number_input(
        "Phí khác (wifi, vệ sinh...)", min_value=0, value=int(prices_db["phi_khac"]), step=10000,
    )
    ap_dung_sau = st.checkbox("Áp dụng cho cả các tháng sau", value=False)
    luu_gia = st.form_submit_button("Lưu đơn giá", type="primary", use_container_width=True)

if luu_gia:
    save_month_prices(
        nam_chon, idx_thang_chon, f_gia_dien, f_gia_nuoc, f_phi_khac, ap_dung_sau
    )
    prices_db = get_month_prices(nam_chon, idx_thang_chon)
    st.sidebar.success("Đã lưu đơn giá")

# Đơn giá dùng để tính tiền luôn là đơn giá đã lưu trong database
prices = {
    "gia_dien": int(prices_db["gia_dien"]),
    "gia_nuoc": int(prices_db["gia_nuoc"]),
    "phi_khac": int(prices_db["phi_khac"]),
}
st.sidebar.caption(
    f"Đang áp dụng: điện {tien(prices['gia_dien'])} đ/kWh, "
    f"nước {tien(prices['gia_nuoc'])} đ/m³, phí khác {tien(prices['phi_khac'])} đ"
)
if prices["gia_dien"] < 100 or prices["gia_nuoc"] < 100:
    st.sidebar.warning(
        "Đơn giá điện hoặc nước đang rất thấp. Hãy nhập đầy đủ, ví dụ 3500 "
        "thay vì 3 (đơn vị là đồng)."
    )

_cfg_hien_tai = lay_cau_hinh_smtp()
if _cfg_hien_tai:
    st.sidebar.caption(f"Email gửi phiếu: {_cfg_hien_tai['user']}")
else:
    st.sidebar.warning("Chưa cấu hình email gửi phiếu")

with st.sidebar.expander("Cấu hình gửi email", expanded=_cfg_hien_tai is None):
    st.caption(
        "Với Gmail, hãy dùng Mật khẩu ứng dụng (App password), không dùng mật khẩu đăng nhập. "
        "Thông tin này chỉ giữ tạm trong phiên làm việc, không lưu vào database."
    )
    st.text_input("Máy chủ SMTP", value="smtp.gmail.com", key="smtp_host")
    st.number_input("Cổng", min_value=1, max_value=65535, value=587, key="smtp_port")
    st.text_input("Email gửi đi", key="smtp_user")
    st.text_input("Mật khẩu ứng dụng", type="password", key="smtp_password")
    st.text_input("Tên người gửi", value=TEN_DAY_TRO, key="smtp_ten")
    st.checkbox(
        "Tự động gửi phiếu PDF qua email mỗi khi bấm 'Lưu thông tin phòng'",
        value=False,
        key="tu_dong_gui",
    )
    if st.button("Kiểm tra kết nối email", key="btn_test_smtp"):
        cfg_test = lay_cau_hinh_smtp()
        if not cfg_test:
            st.error("Hãy điền Email gửi đi và Mật khẩu ứng dụng trước.")
        else:
            with st.spinner("Đang kiểm tra..."):
                ok, thong_bao = kiem_tra_smtp(cfg_test)
            (st.success if ok else st.error)(thong_bao)

# Đảm bảo dữ liệu các phòng của tháng đang chọn đã tồn tại trong database
ensure_records(nam_chon, idx_thang_chon, so_phong)

# ---------- NHẬP DỮ LIỆU ----------
st.header(f"Nhập số liệu - {thang_chon}")

phong = st.selectbox(
    "Chọn phòng cần nhập dữ liệu:",
    range(1, so_phong + 1),
    key=f"selectbox_phong_{suffix}",
)

rec = get_record(nam_chon, idx_thang_chon, phong)
st.subheader(f"Cập nhật: Phòng {phong} ({thang_chon})")

with st.form(key=f"form_{suffix}_{phong}"):
    # Hàng 1: Giá phòng + thông tin người ở
    a1, a2, a3, a4 = st.columns([1.2, 1.6, 1.2, 0.8])
    with a1:
        gia_phong = st.number_input(
            "Giá phòng (VNĐ)", min_value=0, value=int(rec["gia_phong"]), step=50000
        )
    with a2:
        nguoi_o = st.text_input("Họ tên người ở", value=rec["nguoi_o"])
    with a3:
        sdt = st.text_input("Số điện thoại", value=rec["sdt"])
    with a4:
        so_nguoi = st.number_input(
            "Số người ở", min_value=0, max_value=20, value=int(rec["so_nguoi"]), step=1
        )

    email = st.text_input(
        "Email nhận phiếu thu (để gửi PDF)", value=rec["email"],
        placeholder="vidu@gmail.com",
    )

    # Hàng 2: điện nước
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        dien_cu = st.number_input("Số điện CŨ", min_value=0, value=int(rec["dien_cu"]))
    with b2:
        dien_moi = st.number_input("Số điện MỚI", min_value=0, value=int(rec["dien_moi"]))
    with b3:
        nuoc_cu = st.number_input("Số nước CŨ", min_value=0, value=int(rec["nuoc_cu"]))
    with b4:
        nuoc_moi = st.number_input("Số nước MỚI", min_value=0, value=int(rec["nuoc_moi"]))

    # Hàng 3: trạng thái thanh toán
    trang_thai = st.radio(
        "Trạng thái thanh toán",
        TRANG_THAI,
        index=TRANG_THAI.index(rec["trang_thai"]) if rec["trang_thai"] in TRANG_THAI else 0,
        horizontal=True,
    )

    submitted = st.form_submit_button("Lưu thông tin phòng", type="primary")

if submitted:
    save_record(
        nam_chon, idx_thang_chon, phong,
        {
            "gia_phong": gia_phong, "nguoi_o": nguoi_o.strip(), "sdt": sdt.strip(),
            "email": email.strip(),
            "so_nguoi": so_nguoi, "dien_cu": dien_cu, "dien_moi": dien_moi,
            "nuoc_cu": nuoc_cu, "nuoc_moi": nuoc_moi, "trang_thai": trang_thai,
        },
    )
    st.success(f"Đã lưu Phòng {phong} - {thang_chon}")
    rec = get_record(nam_chon, idx_thang_chon, phong)

    if st.session_state.get("tu_dong_gui"):
        cfg_auto = lay_cau_hinh_smtp()
        if not cfg_auto:
            st.warning("Đang bật tự động gửi email nhưng chưa cấu hình tài khoản gửi mail.")
        elif not email_hop_le(rec["email"]):
            st.warning("Phòng này chưa có email hợp lệ nên chưa gửi phiếu.")
        else:
            try:
                pdf_auto = tao_pdf(
                    nam_chon, idx_thang_chon, [(rec, tinh_tien(rec, prices), prices)]
                )
                gui_phieu(cfg_auto, rec, pdf_auto, nam_chon, idx_thang_chon)
                st.success(f"Đã tự động gửi phiếu tới {rec['email']}")
            except Exception as ex:
                st.error(f"Gửi email tự động thất bại: {dich_loi_smtp(ex)}")

# Xuất PDF riêng cho phòng đang chọn
try:
    pdf_phong = tao_pdf(
        nam_chon, idx_thang_chon, [(rec, tinh_tien(rec, prices), prices)]
    )
    st.download_button(
        label=f"Xuất PDF Phòng {phong} - {thang_chon}",
        data=pdf_phong,
        file_name=f"Phieu_Phong_{phong}_{idx_thang_chon:02d}_{nam_chon}.pdf",
        mime="application/pdf",
        key=f"pdf_phong_{suffix}_{phong}",
    )
    if st.button(f"Gửi PDF Phòng {phong} qua email", key=f"send_{suffix}_{phong}"):
        cfg = lay_cau_hinh_smtp()
        if not cfg:
            st.error(THONG_BAO_CHUA_CAU_HINH)
        elif not email_hop_le(rec["email"]):
            st.error("Phòng này chưa có email hợp lệ. Hãy nhập email và bấm 'Lưu thông tin phòng'.")
        else:
            try:
                with st.spinner("Đang gửi email..."):
                    gui_phieu(cfg, rec, pdf_phong, nam_chon, idx_thang_chon)
                st.success(f"Đã gửi phiếu Phòng {phong} tới {rec['email']}")
            except Exception as ex:
                st.error(f"Gửi email thất bại: {dich_loi_smtp(ex)}")
except FileNotFoundError as e:
    st.warning(str(e))

# ---------- BẢNG TỔNG HỢP ----------
st.divider()
st.header(f"Bảng tổng hợp tiền trọ - {thang_chon}")

records = get_records(nam_chon, idx_thang_chon, so_phong)
ket_qua = []
tong_doanh_thu = 0
da_thu = 0
danh_sach_pdf = []

for r in records:
    kq = tinh_tien(r, prices)
    tong_doanh_thu += kq["tong"]
    if r["trang_thai"] == TRANG_THAI[1]:
        da_thu += kq["tong"]
    danh_sach_pdf.append((r, kq, prices))
    ket_qua.append({
        "Phòng": f"Phòng {r['phong']}",
        "Người ở": r["nguoi_o"],
        "SĐT": r["sdt"],
        "Email": r["email"],
        "Giá phòng": r["gia_phong"],
        "Số điện cũ": r["dien_cu"],
        "Số điện mới": r["dien_moi"],
        "Tiền điện": kq["tien_dien"],
        "Số nước cũ": r["nuoc_cu"],
        "Số nước mới": r["nuoc_moi"],
        "Tiền nước": kq["tien_nuoc"],
        "Phí khác": kq["phi_khac"],
        "Tổng tiền": kq["tong"],
        "Thanh toán": r["trang_thai"],
    })

if ket_qua:
    df = pd.DataFrame(ket_qua)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Giá phòng": st.column_config.NumberColumn(format="%d"),
            "Tiền điện": st.column_config.NumberColumn(format="%d"),
            "Tiền nước": st.column_config.NumberColumn(format="%d"),
            "Phí khác": st.column_config.NumberColumn(format="%d"),
            "Tổng tiền": st.column_config.NumberColumn(format="%d"),
        },
    )

    m1, m2, m3 = st.columns(3)
    m1.metric(f"Tổng thu nhập các phòng ({thang_chon})", f"{tong_doanh_thu:,.0f} VNĐ")
    m2.metric("Đã thu", f"{da_thu:,.0f} VNĐ")
    m3.metric("Chưa thu", f"{tong_doanh_thu - da_thu:,.0f} VNĐ")

    st.subheader("Xuất báo cáo")
    e1, e2, e3 = st.columns(3)

    with e1:
        csv = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label=f"Xuất bảng tổng hợp {thang_chon} (CSV)",
            data=csv,
            file_name=f"Bang_Tien_Phong_{idx_thang_chon:02d}_{nam_chon}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with e2:
        try:
            pdf_all = tao_pdf(nam_chon, idx_thang_chon, danh_sach_pdf)
            st.download_button(
                label=f"Xuất PDF tất cả phòng {thang_chon}",
                data=pdf_all,
                file_name=f"Phieu_Tat_Ca_Phong_{idx_thang_chon:02d}_{nam_chon}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        except FileNotFoundError as e:
            st.warning(str(e))

    with e3:
        if os.path.exists(DB_PATH):
            with open(DB_PATH, "rb") as f:
                st.download_button(
                    label="Sao lưu database (.db)",
                    data=f.read(),
                    file_name=f"tien_tro_backup_{datetime.now().strftime('%Y%m%d')}.db",
                    mime="application/octet-stream",
                    use_container_width=True,
                )

    st.subheader("Gửi phiếu qua email")
    co_email = [x for x in danh_sach_pdf if email_hop_le(x[0]["email"])]
    st.caption(f"{len(co_email)}/{len(danh_sach_pdf)} phòng có email hợp lệ")
    if st.button("Gửi PDF cho tất cả phòng có email", disabled=not co_email):
        cfg = lay_cau_hinh_smtp()
        if not cfg:
            st.error(THONG_BAO_CHUA_CAU_HINH)
        else:
            thanh_cong, loi = 0, []
            tien_do = st.progress(0.0)
            for n, item in enumerate(co_email, start=1):
                r_i = item[0]
                try:
                    pdf_i = tao_pdf(nam_chon, idx_thang_chon, [item])
                    gui_phieu(cfg, r_i, pdf_i, nam_chon, idx_thang_chon)
                    thanh_cong += 1
                except Exception as ex:
                    loi.append(f"Phòng {r_i['phong']}: {dich_loi_smtp(ex)}")
                tien_do.progress(n / len(co_email))
            st.success(f"Đã gửi thành công {thanh_cong}/{len(co_email)} phòng")
            for dong_loi in loi:
                st.error(dong_loi)
else:
    st.info("Chưa có dữ liệu phòng nào cho tháng này.")
