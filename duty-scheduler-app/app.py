# ---------------------------------------------
# 依存: ortools==9.8 以上 (requirements.txt に追加済み)
# ---------------------------------------------
from ortools.sat.python import cp_model

def build_schedule(df_raw: pd.DataFrame, year=2025, month=6):
    doctors   = df_raw['Name'].tolist()
    group     = df_raw.set_index('Name')['Group'].to_dict()
    avail     = 1 - df_raw.drop(columns=['Group', 'Name'])          # 1 = 可

    # --- シフト列を日付順に並べる -----------------------------------
    shifts = sorted(
        avail.columns,
        key=lambda c: (int(re.match(r'(\d+)', c).group(1)),
                       int(re.search(r'-(\d)', c).group(1)) if '-' in c else 0)
    )
    day_of = {c: int(re.match(r'(\d+)', c).group(1)) for c in shifts}
    sub_of = {c: int(re.search(r'-(\d)', c).group(1)) if '-' in c else 0 for c in shifts}

    # --- CP-SAT モデル ---------------------------------------------
    model = cp_model.CpModel()
    x = {}      # Duty[d,s]
    y = {}      # Oncall[d,s]

    for d in doctors:
        for s in shifts:
            x[d, s] = model.NewBoolVar(f"x_{d}_{s}")
            y[d, s] = model.NewBoolVar(f"y_{d}_{s}")
            # 勤務不可なら 0 に固定
            if avail.loc[df_raw['Name'] == d, s].iat[0] == 0:
                model.Add(x[d, s] == 0)
                model.Add(y[d, s] == 0)

    # ---- シフト充足制約 -------------------------------------------
    for s in shifts:
        if sub_of[s] == 1:                      # 休日 -1
            model.Add(sum(x[d, s] for d in doctors if group[d] == 0) == 1)
            model.Add(sum(x[d, s] for d in doctors if group[d] == 1) == 1)
            model.Add(sum(y[d, s] for d in doctors) == 0)            # on-call 無し
        else:                                   # 平日/休日 -2
            model.Add(sum(x[d, s] for d in doctors) == 1)            # Duty 1 名
            # Duty が G1 のときだけ G0 on-call 1 名
            model.Add(
                sum(y[d, s] for d in doctors if group[d] == 0)
                == sum(x[d, s] for d in doctors if group[d] == 1)
            )
            model.Add(
                sum(y[d, s] for d in doctors if group[d] == 1) == 0
            )

    # ---- 各医師の回数制限 -----------------------------------------
    for d in doctors:
        duty_cnt   = sum(x[d, s] for s in shifts)
        oncall_cnt = sum(y[d, s] for s in shifts)

        if group[d] == 1:                          # Group 1
            model.Add(duty_cnt == 2)
            model.Add(oncall_cnt == 0)
        else:                                      # Group 0
            model.Add(duty_cnt >= 1)
            model.Add(duty_cnt <= 2)
            model.Add(oncall_cnt <= 2)

    # ---- 目的: とにかく可行解を最速で ------------------------------
    model.Minimize(0)   # 目的を持たない → 最初の可行解で終了

    # ---- Solve -----------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    if solver.Solve(model) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        st.error("割り付け不可でした。可勤務日を見直してください。")
        st.stop()

    # ---- DataFrame 出力 -------------------------------------------
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
