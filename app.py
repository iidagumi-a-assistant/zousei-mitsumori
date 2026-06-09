import streamlit as st
import requests
import urllib.parse
import json
from dataclasses import dataclass, field
from typing import Optional

st.set_page_config(page_title="造成工事 概算見積もり", layout="wide")

# ── 定数・単価マスタ ──────────────────────────────────────────
DEFAULT_UNIT_PRICES = {
    "伐採・抜根（㎡）": 800,
    "表土除去（㎡）": 500,
    "切土（㎥）": 1_500,
    "盛土（㎥）": 2_000,
    "残土処分（㎥）": 4_000,
    "土留め擁壁 L型（m²）": 35_000,
    "土留め擁壁 重力式（m²）": 25_000,
    "整地・転圧（㎡）": 600,
}

# ── API関数 ───────────────────────────────────────────────────

def geocode(address: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    url = "https://msearch.gsi.go.jp/address-search/AddressSearch?q=" + urllib.parse.quote(address)
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if data:
            lon, lat = data[0]["geometry"]["coordinates"]
            title = data[0]["properties"]["title"]
            return lat, lon, title
    except Exception:
        pass
    return None, None, None


def get_elevation(lat: float, lon: float) -> tuple[Optional[float], Optional[str]]:
    url = f"https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php?lon={lon}&lat={lat}&outtype=JSON"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        return data.get("elevation"), data.get("hsrc")
    except Exception:
        pass
    return None, None


# ── 計算ロジック ──────────────────────────────────────────────

def calc_wall_area(height: float, length: float) -> float:
    return height * length


def calc_earthwork_volume(area: float, avg_height: float) -> float:
    """切土・盛土の土量（台形断面近似）"""
    return area * avg_height * 0.5


# ── UI ───────────────────────────────────────────────────────

st.title("造成工事 概算見積もりツール")
st.caption("※ 本ツールの出力は概算です。必ず担当者が内容を確認・精査のうえ顧客へ提出してください。")

tab1, tab2, tab3 = st.tabs(["① 土地情報", "② 隣地との高低差", "③ 見積もり結果"])

# ─────────────────────────────────────────────
# TAB 1：土地情報
# ─────────────────────────────────────────────
with tab1:
    st.subheader("対象地の情報")

    col1, col2 = st.columns(2)
    with col1:
        address_input = st.text_input("住所（住居表示）", placeholder="例：浜松市中央区小池町673-3")
        site_area = st.number_input("土地面積（㎡）", min_value=1.0, value=200.0, step=1.0)

    with col2:
        st.markdown("**または座標を直接入力**")
        manual_lat = st.number_input("緯度", value=0.0, format="%.6f")
        manual_lon = st.number_input("経度", value=0.0, format="%.6f")

    col_btn, col_result = st.columns([1, 3])
    with col_btn:
        run_geocode = st.button("住所から座標・標高を取得", type="primary")

    if run_geocode and address_input:
        with st.spinner("取得中..."):
            lat, lon, title = geocode(address_input)
            if lat:
                elev, src = get_elevation(lat, lon)
                st.session_state["site"] = {"lat": lat, "lon": lon, "elev": elev, "src": src, "title": title}
            else:
                st.error("住所のジオコーディングに失敗しました。住所を確認してください。")

    if "site" in st.session_state:
        s = st.session_state["site"]
        st.success(f"取得完了：{s['title']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("緯度 / 経度", f"{s['lat']:.5f}, {s['lon']:.5f}")
        c2.metric("標高", f"{s['elev']} m")
        c3.metric("データソース", s["src"])
    elif manual_lat != 0.0 and manual_lon != 0.0:
        if st.button("座標から標高を取得"):
            elev, src = get_elevation(manual_lat, manual_lon)
            st.session_state["site"] = {"lat": manual_lat, "lon": manual_lon, "elev": elev, "src": src, "title": "（直接入力）"}

    st.session_state["site_area"] = site_area

    st.divider()
    st.subheader("工事条件")
    col3, col4 = st.columns(2)
    with col3:
        has_trees = st.checkbox("伐採・抜根あり")
        remove_surface = st.checkbox("表土除去あり", value=True)
    with col4:
        transport_dist = st.selectbox("残土搬出距離", ["近距離（5km未満）", "中距離（5〜15km）", "遠距離（15km以上）"])

    dist_factor = {"近距離（5km未満）": 1.0, "中距離（5〜15km）": 1.3, "遠距離（15km以上）": 1.6}
    st.session_state["conditions"] = {
        "has_trees": has_trees,
        "remove_surface": remove_surface,
        "dist_factor": dist_factor[transport_dist],
    }


# ─────────────────────────────────────────────
# TAB 2：隣地との高低差
# ─────────────────────────────────────────────
with tab2:
    st.subheader("隣地の情報（境界辺ごとに入力）")
    st.info("対象地の各辺について、隣地の住所または座標を入力してください。住所から自動で標高を取得し、高低差を算出します。")

    num_sides = st.number_input("境界辺の数", min_value=1, max_value=6, value=3, step=1)

    if "neighbors" not in st.session_state:
        st.session_state["neighbors"] = []

    neighbors = []
    for i in range(int(num_sides)):
        st.markdown(f"**辺 {i+1}**")
        c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
        with c1:
            nb_addr = st.text_input(f"隣地住所（辺{i+1}）", key=f"nb_addr_{i}", placeholder="省略可（座標直接入力も可）")
        with c2:
            nb_len = st.number_input(f"境界長さ（m）", min_value=0.1, value=10.0, step=0.5, key=f"nb_len_{i}")
        with c3:
            nb_lat = st.number_input("緯度", value=0.0, format="%.6f", key=f"nb_lat_{i}")
        with c4:
            nb_lon = st.number_input("経度", value=0.0, format="%.6f", key=f"nb_lon_{i}")

        nb_get = st.button(f"辺{i+1}の標高を取得", key=f"nb_btn_{i}")
        if nb_get:
            if nb_addr:
                lat, lon, title = geocode(nb_addr)
            elif nb_lat != 0.0 and nb_lon != 0.0:
                lat, lon, title = nb_lat, nb_lon, "（直接入力）"
            else:
                lat, lon, title = None, None, None

            if lat:
                elev, src = get_elevation(lat, lon)
                st.session_state[f"nb_result_{i}"] = {"elev": elev, "src": src, "title": title, "len": nb_len}
                st.success(f"標高 {elev} m（{src}）/ {title}")
            else:
                st.error("住所または座標を入力してください。")

        if f"nb_result_{i}" in st.session_state:
            nb_r = st.session_state[f"nb_result_{i}"]
            site_elev = st.session_state.get("site", {}).get("elev")
            if site_elev is not None and nb_r["elev"] is not None:
                diff = nb_r["elev"] - site_elev
                direction = "隣地が高い（盛土または切土が必要）" if diff > 0 else "隣地が低い（擁壁が必要な場合あり）" if diff < 0 else "同一標高"
                col_a, col_b = st.columns(2)
                col_a.metric(f"辺{i+1} 高低差", f"{diff:+.1f} m", delta_color="off")
                col_b.write(f"→ {direction}")

                wall_type = st.selectbox(
                    f"辺{i+1} 擁壁種別",
                    ["なし", "L型擁壁", "重力式擁壁"],
                    key=f"wall_type_{i}"
                )
                neighbors.append({
                    "id": i + 1,
                    "elev": nb_r["elev"],
                    "diff": diff,
                    "len": nb_len,
                    "wall_type": wall_type,
                    "src": nb_r["src"],
                })
        st.divider()

    st.session_state["neighbors"] = neighbors


# ─────────────────────────────────────────────
# TAB 3：見積もり結果
# ─────────────────────────────────────────────
with tab3:
    st.subheader("単価マスタ（必要に応じて修正可）")

    with st.expander("単価を確認・編集する", expanded=False):
        prices = {}
        for k, v in DEFAULT_UNIT_PRICES.items():
            prices[k] = st.number_input(k, value=v, step=100, key=f"price_{k}")
    prices = {k: st.session_state.get(f"price_{k}", v) for k, v in DEFAULT_UNIT_PRICES.items()}

    st.divider()
    st.subheader("概算見積もり")

    site_area = st.session_state.get("site_area", 0)
    conditions = st.session_state.get("conditions", {})
    neighbors = st.session_state.get("neighbors", [])
    site_elev = st.session_state.get("site", {}).get("elev")

    if not neighbors or site_elev is None:
        st.warning("① 土地情報 と ② 隣地との高低差 を先に入力してください。")
    else:
        items = []

        # 伐採・抜根
        if conditions.get("has_trees"):
            amt = site_area * prices["伐採・抜根（㎡）"]
            items.append({"工種": "伐採・抜根", "数量": site_area, "単位": "㎡", "単価": prices["伐採・抜根（㎡）"], "金額": amt})

        # 表土除去
        if conditions.get("remove_surface"):
            amt = site_area * prices["表土除去（㎡）"]
            items.append({"工種": "表土除去", "数量": site_area, "単位": "㎡", "単価": prices["表土除去（㎡）"], "金額": amt})

        # 各辺の擁壁・土工
        total_cut = 0.0
        total_fill = 0.0
        for nb in neighbors:
            diff = nb["diff"]
            length = nb["len"]
            abs_diff = abs(diff)

            # 土量（概算：台形断面近似）
            vol = calc_earthwork_volume(length * abs_diff * 0.5, abs_diff)  # 簡易近似

            if diff > 0:
                # 隣地が高い → 盛土
                total_fill += vol
            elif diff < 0:
                # 隣地が低い → 切土・擁壁
                total_cut += vol

                wall_key = {"L型擁壁": "土留め擁壁 L型（m²）", "重力式擁壁": "土留め擁壁 重力式（m²）"}.get(nb["wall_type"])
                if wall_key:
                    wall_area_val = calc_wall_area(abs_diff, length)
                    amt = wall_area_val * prices[wall_key]
                    items.append({
                        "工種": f"擁壁工（辺{nb['id']} / {nb['wall_type']}）",
                        "数量": round(wall_area_val, 1),
                        "単位": "m²",
                        "単価": prices[wall_key],
                        "金額": amt,
                    })

        # 切土・盛土
        if total_cut > 0:
            amt = total_cut * prices["切土（㎥）"]
            items.append({"工種": "切土", "数量": round(total_cut, 1), "単位": "㎥", "単価": prices["切土（㎥）"], "金額": amt})
        if total_fill > 0:
            amt = total_fill * prices["盛土（㎥）"]
            items.append({"工種": "盛土", "数量": round(total_fill, 1), "単位": "㎥", "単価": prices["盛土（㎥）"], "金額": amt})

        # 残土処分（切土量 × 距離係数）
        if total_cut > 0:
            amt = total_cut * prices["残土処分（㎥）"] * conditions.get("dist_factor", 1.0)
            items.append({"工種": "残土処分", "数量": round(total_cut, 1), "単位": "㎥", "単価": int(prices["残土処分（㎥）"] * conditions.get("dist_factor", 1.0)), "金額": amt})

        # 整地・転圧
        amt = site_area * prices["整地・転圧（㎡）"]
        items.append({"工種": "整地・転圧", "数量": site_area, "単位": "㎡", "単価": prices["整地・転圧（㎡）"], "金額": amt})

        if items:
            import pandas as pd
            df = pd.DataFrame(items)
            df["金額"] = df["金額"].astype(int)
            df["単価"] = df["単価"].astype(int)
            df["数量"] = df["数量"].apply(lambda x: f"{x:,.1f}")

            st.dataframe(
                df.style.format({"金額": "{:,}", "単価": "{:,}"}),
                use_container_width=True,
                hide_index=True,
            )

            subtotal = sum(i["金額"] for i in items)
            tax = int(subtotal * 0.1)
            total = subtotal + tax

            col1, col2, col3 = st.columns(3)
            col1.metric("小計（税抜）", f"¥{subtotal:,}")
            col2.metric("消費税（10%）", f"¥{tax:,}")
            col3.metric("合計（税込）", f"¥{total:,}")

            st.divider()
            st.subheader("担当者チェック欄")
            st.text_area("確認コメント・修正事項", placeholder="例：擁壁高さの再確認が必要、残土処分先の確認要　など", height=100, key="review_comment")

            flags = []
            for nb in neighbors:
                if abs(nb["diff"]) > 2.0:
                    flags.append(f"⚠️ 辺{nb['id']}：高低差 {nb['diff']:+.1f}m → 構造計算・確認申請が必要な可能性あり")
            if total_cut + total_fill > 500:
                flags.append("⚠️ 土量が大きい（500㎥超）→ 宅造法・土砂条例の確認推奨")

            if flags:
                st.subheader("要確認フラグ")
                for f in flags:
                    st.warning(f)
            else:
                st.success("自動チェック：特記事項なし（ただし最終判断は担当者が行ってください）")
