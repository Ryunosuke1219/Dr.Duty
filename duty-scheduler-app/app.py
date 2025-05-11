import streamlit as st, pandas as pd, re, random, datetime, tempfile, os, io, csv
from collections import defaultdict
from ortools.sat.python import cp_model
from utils.generate_calendar import generate_pretty_calendar
from utils.loose_scheduler import build_schedule as build_schedule_loose

# ---------- スケジューラ（3 日間隔 + 5 ルール） ----------
def build_schedule(df_raw: pd.DataFrame, year: int, month: int):
    doctors = df_raw["Name"].tolist()
    group   = df_raw.set_index("Name")["Group"].to_dict()
    avail   = 1 - df_raw.drop(columns=["Group", "Name"])          # 1 = 可

    # 列を 1-1,1-2,2… の順に並べ替え
    shifts = sorted(
        avail.columns,
        key=lambda c: (int(re.match(r"(\d+)", c).group(1)),
                       int(re.search(r"-(\d)", c).group(1)) if "-" in c else 0)
    )
    day_of = {c: int(re.match(r"(\d+)", c).group(1)) for c in shifts}
    sub_of = {c: int(re.search(r"-(\d)", c).group(1)) if "-" in c else 0 for c in shifts}

    # ---------- CP-SAT ----------
    m = cp_model.CpModel()
    x, y = {}, {}     # Duty, Oncall

    for d in doctors:
        for s in shifts:
            x[d, s] = m.NewBoolVar(f"x_{d}_{s}")
            y[d, s] = m.NewBoolVar(f"y_{d}_{s}")
            if avail.loc[df_raw["Name"] == d, s].iat[0] == 0:
                m.Add(x[d, s] == 0)
                m.Add(y[d, s] == 0)

    # ①② すべて埋める & G1 Duty→G0 OC
    for s in shifts:
        if sub_of[s] == 1:                       # 休日-1
            m.Add(sum(x[d, s] for d in doctors if group[d] == 0) == 1)
            m.Add(sum(x[d, s] for d in doctors if group[d] == 1) == 1)
            m.Add(sum(y[d, s] for d in doctors) == 0)
        else:
            m.Add(sum(x[d, s] for d in doctors) == 1)
            m.Add(
                sum(y[d, s] for d in doctors if group[d] == 0)
                == sum(x[d, s] for d in doctors if group[d] == 1)
            )
            m.Add(sum(y[d, s] for d in doctors if group[d] == 1) == 0)

    # ③④⑤ 回数上限
    for d in doctors:
        duty_cnt   = sum(x[d, s] for s in shifts)
        oncall_cnt = sum(y[d, s] for s in shifts)
        if group[d] == 1:
            m.Add(duty_cnt == 2)
            m.Add(oncall_cnt == 0)
        else:
            m.Add(duty_cnt >= 1)
            m.Add(duty_cnt <= 2)
            m.Add(oncall_cnt <= 2)

    # 3 日間隔
    for d in doctors:
        for i, s1 in enumerate(shifts):
            for s2 in shifts[i + 1:]:
                if 1 <= day_of[s2] - day_of[s1] <= 2:
                    m.Add(x[d, s1] + y[d, s1] + x[d, s2] + y[d, s2] <= 1)

    # 可行解だけ探す
    m.Minimize(0)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    if solver.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        st.error("割り付け不可：可勤務日かルールを見直してください。")
        st.stop()

    # DataFrame 生成
    rows = []
    for s in shifts:
        rows.append({
            "Shift": s,
            "Duty_G0": ", ".join(d for d in doctors if solver.Value(x[d, s]) and group[d] == 0),
            "Duty_G1": ", ".join(d for d in doctors if solver.Value(x[d, s]) and group[d] == 1),
            "Oncall_G0": ", ".join(d for d in doctors if solver.Value(y[d, s]))
        })
    schedule_df = pd.DataFrame(rows)

    summary_df = pd.DataFrame({
        "Group":  [group[d] for d in doctors],
        "Duty":   [sum(solver.Value(x[d, s]) for s in shifts) for d in doctors],
        "Oncall": [sum(solver.Value(y[d, s]) for s in shifts) for d in doctors],
    }, index=doctors)

    return schedule_df, summary_df

# ---------- Streamlit UI ----------
st.set_page_config(page_title="Duty Scheduler", layout="centered")
st.title("Doctor Duty Scheduler")

csv_file = st.file_uploader("当直希望 CSV (0=可/1=NG)", type="csv")
col1, col2 = st.columns(2)
with col1:
    year = st.number_input("年", min_value=2020, max_value=2100, value=datetime.date.today().year, step=1)
with col2:
    month = st.number_input("月", min_value=1, max_value=12, value=datetime.date.today().month, step=1)

if csv_file:
    df_raw = pd.read_csv(csv_file, encoding="cp932")

    mode = st.radio("割付モードを選択", ["strict", "loose"], horizontal=True)

    if mode == "strict":
        sched, summary = build_schedule(df_raw, int(year), int(month))
    else:  # loose
        sched, summary = build_schedule_loose(df_raw, int(year), int(month))
    # ▲▲ ここまで ▲▲

    # ---------- 表示 ----------
    st.subheader("Schedule")
    st.dataframe(sched, hide_index=True, use_container_width=True)
    st.subheader("Summary")
    st.dataframe(summary, use_container_width=True)

    # ---------- 追加 CSV (Duty=3, OC=4) ----------
    df_ann = df_raw.copy()
    warnings = []
    for _, r in sched.iterrows():
        shift = r["Shift"]
        # Duty
        for name in (r["Duty_G0"] + "," + r["Duty_G1"]).split(","):
            name = name.strip()
            if name:
                if df_ann.loc[df_ann["Name"] == name, shift].iat[0] != 0:
                    warnings.append(f"{name} {shift} は元が 0 でない")
                df_ann.loc[df_ann["Name"] == name, shift] = 3
        # OC
        if r["Oncall_G0"]:
            name = r["Oncall_G0"]
            if df_ann.loc[df_ann["Name"] == name, shift].iat[0] != 0:
                warnings.append(f"{name} {shift} (OC) は元が 0 でない")
            df_ann.loc[df_ann["Name"] == name, shift] = 4

    # ---------- ダウンロード ----------
    with tempfile.TemporaryDirectory() as tmp:
        # 1) Excel
        excel_path = os.path.join(tmp, "schedule.xlsx")
        with pd.ExcelWriter(excel_path) as w:
            sched.to_excel(w, sheet_name="Schedule", index=False)
            summary.to_excel(w, sheet_name="Summary")
        with open(excel_path, "rb") as f:
            st.download_button("Excel をダウンロード", f, "schedule.xlsx")

        # 2) カレンダー
        cal_path = os.path.join(tmp, "pretty_calendar.xlsx")
        generate_pretty_calendar(sched, int(year), int(month), set(), cal_path)
        with open(cal_path, "rb") as f:
            st.download_button("カレンダーをダウンロード", f, "pretty_calendar.xlsx")

        # 3) 注釈入り CSV
        csv_path = os.path.join(tmp, "availability_annotated.csv")
        df_ann.to_csv(csv_path, index=False, encoding="cp932")
        with open(csv_path, "rb") as f:
            st.download_button("注釈入り CSV をダウンロード", f, "availability_annotated.csv")

    # 元が 0 でなかったセルがあれば警告
    if warnings:
        st.warning("上書き時に元が 0 でなかったセルがあります:\\n" + "\\n".join(warnings))
else:
    st.info("CSV をアップロードして年・月を指定してください。")
