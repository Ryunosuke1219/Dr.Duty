
import streamlit as st, pandas as pd, re, random, datetime, tempfile, os
from collections import defaultdict
from utils.generate_calendar import generate_pretty_calendar

SPACE=3  # min days between any duty/oncall

def shift_sort_key(col):
    day=int(re.match(r'(\d+)',col).group(1))
    sub=int(re.search(r'-(\d)',col).group(1)) if '-' in col else 0
    return (day,sub)

from ortools.sat.python import cp_model

def build_schedule(df_raw: pd.DataFrame, year: int, month: int,
                   oncall_weight: float = 0.3):
    """
    CP-SAT で Duty / on-call を同時最適化。
    回数バランス (max−min) を最小化 → 次に希望充足数を最大化。
    """
    doctors   = df_raw['Name'].tolist()
    group_map = df_raw.set_index('Name')['Group'].to_dict()
    avail     = 1 - df_raw.drop(columns=['Group', 'Name'])    # 1 = 勤務可
    shifts    = sorted(avail.columns, key=lambda c: (
        int(re.match(r'(\d+)', c).group(1)),
        int(re.search(r'-(\d)', c).group(1)) if '-' in c else 0))
    day_of    = {c: int(re.match(r'(\d+)', c).group(1)) for c in shifts}
    sub_of    = {c: int(re.search(r'-(\d)', c).group(1)) if '-' in c else 0 for c in shifts}

    # --- CP-SAT モデル ---
    m = cp_model.CpModel()
    x = {}       # Duty vars
    y = {}       # on-call vars

    for d in doctors:
        for s in shifts:
            x[d, s] = m.NewBoolVar(f"x_{d}_{s}")
            y[d, s] = m.NewBoolVar(f"y_{d}_{s}")

            # 勤務可日にしか立てない
            if avail.loc[df_raw['Name'] == d, s].iat[0] == 0:
                m.Add(x[d, s] == 0)
                m.Add(y[d, s] == 0)

    # --- シフト充足制約 ---
    for s in shifts:
        # Duty
        if sub_of[s] == 1:                             # -1 … G0 + G1
            m.Add(sum(x[d, s] for d in doctors if group_map[d] == 0) == 1)
            m.Add(sum(x[d, s] for d in doctors if group_map[d] == 1) == 1)
            m.Add(sum(y[d, s] for d in doctors) == 0)  # on-call なし
        else:                                          # single 1 名
            m.Add(sum(x[d, s] for d in doctors) == 1)
            # on-call = Duty が G1 のときだけ G0 1 名
            m.Add(
                sum(y[d, s] for d in doctors if group_map[d] == 0)
                == sum(x[d, s] for d in doctors if group_map[d] == 1)
            )
            m.Add(
                sum(y[d, s] for d in doctors if group_map[d] == 1) == 0
            )

    # --- 3 日間隔＆週 1 回＆ -1 月 1 回 制約 ---
    SPACE = 3
    week_idx = lambda day: (day - 1) // 7
    for d in doctors:
        for i, s1 in enumerate(shifts):
            day1 = day_of[s1]
            # 3 日間隔
            for s2 in shifts[i + 1:]:
                if 1 <= day_of[s2] - day1 < SPACE:
                    m.Add(x[d, s1] + y[d, s1] + x[d, s2] + y[d, s2] <= 1)

        # 週 1 回 Duty 制限
        for w in range(5):
            m.Add(
                sum(x[d, s] for s in shifts if week_idx(day_of[s]) == w) <= 1
            )

        # -1 列は月 1 回
        m.Add(
            sum(x[d, s] for s in shifts if sub_of[s] == 1) <= 1
        )

    # --- 回数バランス用の最大・最小変数 ---
    total = {}
    for d in doctors:
        total[d] = m.NewIntVar(0, 20, f"total_{d}")
        m.Add(
            total[d] == sum(x[d, s] + int(oncall_weight * 10) * y[d, s]
                            for s in shifts)
        )

    max_total = m.NewIntVar(0, 20, "max_total")
    min_total = m.NewIntVar(0, 20, "min_total")
    m.AddMaxEquality(max_total, [total[d] for d in doctors])
    m.AddMinEquality(min_total, [total[d] for d in doctors])

    # --- 目的 1: max_total − min_total を最小化 ---
    balance_span = m.NewIntVar(0, 20, "span")
    m.Add(balance_span == max_total - min_total)
    m.Minimize(balance_span)

    # --- 目的 2: 希望充足数（可／不可 0→1 のみ）を最大化 ---
    #    CP-SAT は一次目的が満たされた後、二次目的を最大化してくれる
    m.Maximize(sum(x[d, s] + y[d, s] for d in doctors for s in shifts))

    # --- Solve ---
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15
    solver.parameters.num_search_workers = 8
    if solver.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        st.error("最適解が得られませんでした…")
        st.stop()

    # --- 結果 DataFrame ---
    rows = []
    for s in shifts:
        rows.append({
            "Shift": s,
            "Duty_G0": ", ".join(d for d in doctors if solver.Value(x[d, s]) and group_map[d] == 0),
            "Duty_G1": ", ".join(d for d in doctors if solver.Value(x[d, s]) and group_map[d] == 1),
            "Oncall_G0": ", ".join(d for d in doctors if solver.Value(y[d, s]))
        })
    schedule_df = pd.DataFrame(rows)

    summary_df = pd.DataFrame({
        "Group":   [group_map[d] for d in doctors],
        "Duty":    [sum(solver.Value(x[d, s]) for s in shifts) for d in doctors],
        "Oncall":  [sum(solver.Value(y[d, s]) for s in shifts) for d in doctors],
    }, index=doctors)
    summary_df["Total"] = summary_df["Duty"] + oncall_weight * summary_df["Oncall"]

    return schedule_df, summary_df


st.title("Doctor Duty Scheduler")
uploaded=st.file_uploader("当直希望 CSV をアップロード (0=可/1=NG)", type=["csv"])
if uploaded:
    df=pd.read_csv(uploaded, encoding='cp932')
    sched, summary = build_schedule(df, 2025, 6)
    st.subheader("Schedule")
    st.dataframe(sched, hide_index=True, use_container_width=True)
    st.subheader("Summary")
    st.dataframe(summary, use_container_width=True)
    # Downloads
    with tempfile.TemporaryDirectory() as tmp:
        excel_path=os.path.join(tmp,"schedule.xlsx")
        with pd.ExcelWriter(excel_path) as w:
            sched.to_excel(w, sheet_name="Schedule", index=False)
            summary.to_excel(w, sheet_name="Summary")
        with open(excel_path,"rb") as f:
            st.download_button("Excel をダウンロード", f, file_name="schedule.xlsx")
        cal_path=os.path.join(tmp,"pretty_calendar.xlsx")
        generate_pretty_calendar(sched, 2025, 6, set(), cal_path)
        with open(cal_path,"rb") as f:
            st.download_button("カレンダーをダウンロード", f, file_name="pretty_calendar.xlsx")
else:
    st.info("CSV をアップロードしてください。")
