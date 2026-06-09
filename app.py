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

tab1, tab2, tab3, tab4 = st.tabs(["① 土地情報", "② 隣地との高低差", "③ 法令チェック", "④ 見積もり結果"])

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
# TAB 3：法令チェック
# ─────────────────────────────────────────────
with tab3:
    st.subheader("法令チェックリスト")
    st.info(
        "各項目を担当者が確認し、該当する場合はチェックを入れてください。"
        "チェック項目は見積もり結果の「要確認事項」として出力されます。"
    )

    st.markdown("---")

    # ── 国法 ─────────────────────────────────────
    st.markdown("### 【国法】")

    st.markdown("#### 盛土規制法（宅地造成及び特定盛土等規制法）※2023年5月施行")
    st.caption("旧・宅地造成等規制法が大幅改正。全国の危険箇所を対象に規制エリアが拡大されました。")
    law_checks = {}

    law_checks["盛土規制法①"] = st.checkbox(
        "宅地造成等工事規制区域 または 特定盛土等規制区域 内である",
        help="静岡県・愛知県ともに指定区域あり。市町村窓口または静岡県/愛知県のGISで確認。"
    )
    if law_checks["盛土規制法①"]:
        st.warning("許可または届出が必要です（切土2m超・盛土1m超・面積500㎡超のいずれかで許可申請）")

    law_checks["盛土規制法②"] = st.checkbox(
        "盛土高さ1m超、または切土高さ2m超の工事がある",
        help="規制区域内では許可申請が必要。区域外でも特定盛土等に該当する場合あり。"
    )
    law_checks["盛土規制法③"] = st.checkbox(
        "造成面積が500㎡を超える",
        help="規制区域内では500㎡超で許可申請対象。"
    )

    st.markdown("#### 都市計画法（開発許可）")
    law_checks["都計法①"] = st.checkbox(
        "市街化区域内で、開発面積が1,000㎡以上である",
        help="市街化区域では1,000㎡以上で開発許可が必要（浜松市・磐田市とも同様）。"
    )
    law_checks["都計法②"] = st.checkbox(
        "市街化調整区域内の土地である",
        help="原則として開発行為は不可。例外許可（農家住宅・既存宅地等）の確認が必要。"
    )
    if law_checks["都計法②"]:
        st.warning("市街化調整区域：開発許可の例外要件を事前に確認してください。")

    st.markdown("#### 農地法")
    law_checks["農地法①"] = st.checkbox(
        "対象地に農地（田・畑）が含まれる",
        help="農地転用許可（4条：自己転用、5条：転用目的の売買）が必要。2ha以上は農林水産大臣許可。"
    )
    if law_checks["農地法①"]:
        st.warning("農業委員会への転用許可申請が必要です。市街化区域内なら届出のみ。")

    st.markdown("#### 砂防法・急傾斜地崩壊危険区域")
    law_checks["砂防①"] = st.checkbox(
        "砂防指定地内である",
        help="静岡県・愛知県ともに天竜川水系等を中心に指定あり。静岡県砂防課または各市窓口で確認。"
    )
    law_checks["急傾斜①"] = st.checkbox(
        "急傾斜地崩壊危険区域内または隣接している",
        help="30度以上の斜面が対象。静岡県砂防課の指定区域図で確認。"
    )
    if law_checks["砂防①"] or law_checks["急傾斜①"]:
        st.warning("行為許可申請が必要です。静岡県または愛知県の担当窓口に確認してください。")

    st.markdown("#### 森林法")
    law_checks["森林法①"] = st.checkbox(
        "保安林または林地開発許可対象地（1ha以上の森林）が含まれる",
        help="保安林内の開発は原則不可。林地開発は1ha以上で県知事許可が必要。"
    )

    st.markdown("---")

    # ── 静岡県条例 ────────────────────────────────
    st.markdown("### 【静岡県条例】（浜松市・磐田市・袋井市など）")

    st.markdown("#### 静岡県土砂の採取等に関する条例（土砂条例）")
    law_checks["静岡土砂①"] = st.checkbox(
        "残土・土砂の搬出量が500㎥以上になる見込みがある",
        help="静岡県では500㎥以上の土砂搬出・堆積に届出が必要。搬出先の確認も要する。"
    )

    st.markdown("#### 静岡県宅地造成工事規制区域（県指定）")
    law_checks["静岡宅造①"] = st.checkbox(
        "県指定の宅地造成工事規制区域内である（盛土規制法の区域指定とは別に県が指定している区域）",
        help="盛土規制法の区域と重複する場合もあるが、県独自の指定区域が存在する。静岡県建築安全推進課で確認。"
    )

    st.markdown("---")

    # ── 浜松市条例 ────────────────────────────────
    st.markdown("### 【浜松市条例】")

    st.markdown("#### 浜松市宅地開発等に関する条例")
    law_checks["浜松宅開①"] = st.checkbox(
        "浜松市内で開発面積が500㎡以上（または戸建て3区画以上の分譲）である",
        help="浜松市独自の宅地開発指導要綱・条例による事前協議・審査が必要。政令指定都市のため独自基準あり。"
    )
    law_checks["浜松宅開②"] = st.checkbox(
        "浜松市の土砂災害警戒区域・特別警戒区域内または隣接している",
        help="浜松市防災マップで確認。特別警戒区域では建築確認に制限あり。"
    )

    st.markdown("---")

    # ── 磐田市・袋井市 ───────────────────────────
    st.markdown("### 【磐田市・袋井市】")

    law_checks["磐田①"] = st.checkbox(
        "磐田市の開発行為に関する指導要綱の協議対象である（市街化区域内300㎡以上等）",
        help="磐田市では都市計画法の許可基準より小さい規模でも事前協議を求める場合あり。磐田市都市計画課に確認。"
    )
    law_checks["袋井①"] = st.checkbox(
        "袋井市の宅地開発指導要綱の協議対象である",
        help="袋井市でも独自の事前協議制度あり。袋井市都市計画課に確認。"
    )

    st.markdown("---")

    # ── 愛知県東部 ───────────────────────────────
    st.markdown("### 【愛知県東部（豊橋・豊川・新城エリア）】")

    law_checks["愛知宅造①"] = st.checkbox(
        "愛知県の宅地造成工事規制区域内である",
        help="愛知県でも盛土規制法に基づく区域指定あり。愛知県建設局または各市建築指導課で確認。"
    )
    law_checks["愛知土砂①"] = st.checkbox(
        "愛知県土砂の流出防止等に関する条例の対象となる工事である",
        help="1,000㎡以上の土地の形質変更で届出が必要。愛知県農林基盤局砂防課で確認。"
    )

    st.session_state["law_checks"] = law_checks

    # チェックされた項目のサマリー表示
    checked_items = [k for k, v in law_checks.items() if v]
    st.markdown("---")
    st.subheader("チェック結果サマリー")
    if checked_items:
        st.error(f"該当あり：{len(checked_items)} 項目。見積もり提出前に各担当窓口への確認・申請手続きを行ってください。")
        for k in checked_items:
            st.write(f"- {k}")
    else:
        st.success("現時点でチェックされた項目はありません。")


# ─────────────────────────────────────────────
# TAB 4：見積もり結果
# ─────────────────────────────────────────────
with tab4:
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
                # 隣地が低い → 切土
                total_cut += vol

            # 擁壁：高低差がある辺で種別が選択されていれば計上
            if abs_diff > 0:
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

            # 法令チェックで該当した項目を追加
            law_checks = st.session_state.get("law_checks", {})
            law_labels = {
                "盛土規制法①": "盛土規制法：宅地造成等工事規制区域または特定盛土等規制区域内 → 許可申請の要否を確認",
                "盛土規制法②": "盛土規制法：盛土1m超または切土2m超の工事あり → 許可申請対象の可能性",
                "盛土規制法③": "盛土規制法：造成面積500㎡超 → 許可申請対象の可能性",
                "都計法①": "都市計画法：市街化区域内1,000㎡以上 → 開発許可申請が必要",
                "都計法②": "都市計画法：市街化調整区域内 → 開発可否の事前確認が必要",
                "農地法①": "農地法：農地転用許可（4条・5条）の申請が必要",
                "砂防①": "砂防法：砂防指定地内 → 行為許可申請が必要",
                "急傾斜①": "急傾斜地崩壊危険区域：行為許可申請が必要",
                "森林法①": "森林法：保安林または林地開発許可（1ha以上）の申請が必要",
                "静岡土砂①": "静岡県土砂条例：500㎥以上の残土搬出 → 届出が必要",
                "静岡宅造①": "静岡県宅地造成工事規制区域内 → 県窓口での手続き確認が必要",
                "浜松宅開①": "浜松市宅地開発条例：500㎡以上または3区画以上 → 事前協議が必要",
                "浜松宅開②": "浜松市土砂災害特別警戒区域内 → 建築制限・開発制限の確認が必要",
                "磐田①": "磐田市開発行為指導要綱：事前協議の対象となる可能性",
                "袋井①": "袋井市宅地開発指導要綱：事前協議の対象となる可能性",
                "愛知宅造①": "愛知県宅地造成工事規制区域内 → 許可申請の要否を確認",
                "愛知土砂①": "愛知県土砂流出防止条例：1,000㎡以上の形質変更 → 届出が必要",
            }
            for key, label in law_labels.items():
                if law_checks.get(key):
                    flags.append(f"📋 【法令】{label}")

            if flags:
                st.subheader("要確認フラグ")
                for f in flags:
                    st.warning(f)
            else:
                st.success("自動チェック：特記事項なし（ただし最終判断は担当者が行ってください）")
