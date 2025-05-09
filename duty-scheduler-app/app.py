
import streamlit as st, pandas as pd, re, random, datetime, tempfile, os
from collections import defaultdict
from utils.generate_calendar import generate_pretty_calendar

SPACE=3  # min days between any duty/oncall

def shift_sort_key(col):
    day=int(re.match(r'(\d+)',col).group(1))
    sub=int(re.search(r'-(\d)',col).group(1)) if '-' in col else 0
    return (day,sub)

from ortools.sat.python import cp_model

def build_schedule(df_raw: pd.DataFrame, year=2025, month=6):
    # ---------- データ準備 ----------
    doctors   = df_raw['Name'].tolist()
    group     = df_raw.set_index('Name')['Group'].to_dict()
    avail     = 1 - df_raw.drop(columns=['Group', 'Name'])            # 1 = 勤務可

    # 列を日付順 (1-1,1-2,2 …) に並べ替え
    shifts = sorted(
        avail.columns,
        key=lambda c: (int(re.match(r'(\d+)', c).group(1)),
                       int(re.search(r'-(\d)', c).group(1)) if '-' in c else 0)
    )
    day_of = {c: int(re.match(r'(\d+)', c).group(1)) for c in shifts}
    sub_of = {c: int(re.search(r'-(\d)', c).group(1)) if '-' in c else 0 for c in shifts}

    # ---------- CP-SAT モデル ----------
    m      = cp_model.CpModel()
    x, y   = {}, {}   # Duty[d,s], Oncall[d,s]

    for d in doctors:
        for s in shifts:
            x[d, s] = m.NewBoolVar(f"x_{d}_{s}")   # Duty
            y[d, s] = m.NewBoolVar(f"y_{d}_{s}")   # On-call
            if avail.loc[df_raw['Name'] == d, s].iat[0] == 0:
                m.Add(x[d, s] == 0)
                m.Add(y[d, s] == 0)

    # ---- シフト充足（ルール①②） ----
    for s in shifts:
        if sub_of[s] == 1:                                   # 休日-1
            m.Add(sum(x[d, s] for d in doctors if group[d] == 0) == 1)
            m.Add(sum(x[d, s] for d in doctors if group[d] == 1) == 1)
            m.Add(sum(y[d, s] for d in doctors) == 0)
        else:                                                # 平日 / 休日-2
            m.Add(sum(x[d, s] for d in doctors) == 1)
            # Duty が G1 → G0 oncall 1 名
            m.Add(
                sum(y[d, s] for d in doctors if group[d] == 0)
                == sum(x[d, s] for d in doctors if group[d] == 1)
            )
            m.Add(sum(y[d, s] for d in doctors if group[d] == 1) == 0)

    # ---- 回数上限（ルール③④⑤） ----
    for d in doctors:
        duty_cnt   = sum(x[d, s] for s in shifts)
        oncall_cnt = sum(y[d, s] for s in shifts)

        if group[d] == 1:                 # Group1
            m.Add(duty_cnt == 2)
            m.Add(oncall_cnt == 0)
        else:                             # Group0
            m.Add(duty_cnt >= 1)
            m.Add(duty_cnt <= 2)
            m.Add(oncall_cnt <= 2)

    # ---- ★ 3 日間隔制約 ★ ----
    for d in doctors:
        for i, s1 in enumerate(shifts):
            for s2 in shifts[i + 1:]:
                if 1 <= day_of[s2] - day_of[s1] <= 2:        # 1日 or 2日差
                    m.Add(x[d, s1] + y[d, s1] + x[d, s2] + y[d, s2] <= 1)

    # ---- 可行解を最速で ----
    m.Minimize(0)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        st.error("制約を満たす割り付けが見つかりませんでした。")
        st.stop()

    # ---------- DataFrame 出力 ----------
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
