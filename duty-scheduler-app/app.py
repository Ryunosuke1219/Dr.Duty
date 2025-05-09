
import streamlit as st, pandas as pd, re, random, datetime, tempfile, os
from collections import defaultdict
from utils.generate_calendar import generate_pretty_calendar

SPACE=3  # min days between any duty/oncall

def shift_sort_key(col):
    day=int(re.match(r'(\d+)',col).group(1))
    sub=int(re.search(r'-(\d)',col).group(1)) if '-' in col else 0
    return (day,sub)

from ortools.sat.python import cp_model

def build_schedule(df_raw: pd.DataFrame, year: int, month: int):
    """
    シンプルな greedy 版：
      • G1 Duty = 2 固定
      • G0 Duty 1–2 回、on-call 上限 2 回
      • 連続勤務 3 日空け / 週 1 回 / 休日-1 Duty 月 1 回 を守る
    """
    doctors   = df_raw['Name'].tolist()
    group     = df_raw.set_index('Name')['Group'].to_dict()
    avail     = 1 - df_raw.drop(columns=['Group', 'Name'])

    # --- シフト列を日付順 ----------
    shifts = sorted(
        avail.columns,
        key=lambda c: (int(re.match(r'(\d+)', c).group(1)),
                       int(re.search(r'-(\d)', c).group(1)) if '-' in c else 0)
    )
    day_of  = {c: int(re.match(r'(\d+)', c).group(1)) for c in shifts}
    sub_of  = {c: int(re.search(r'-(\d)', c).group(1)) if '-' in c else 0 for c in shifts}

    # --- 各種カウンタ ---------------
    duty   = defaultdict(dict)
    oncall = {}
    duty_cnt   = defaultdict(int)
    oncall_cnt = defaultdict(int)
    last_work  = {d: -10 for d in doctors}
    weekly_cnt = defaultdict(lambda: defaultdict(int))
    holiday1_cnt = defaultdict(int)

    SPACE = 3
    week_idx = lambda day: (day - 1) // 7

    def can_work(d, col):
        day = day_of[col]
        return (
            avail.loc[df_raw['Name'] == d, col].iat[0] == 1 and
            day - last_work[d] >= SPACE and
            weekly_cnt[d][week_idx(day)] == 0 and
            (holiday1_cnt[d] < 1 if sub_of[col] == 1 else True)
        )

    # --- ① Group1 Duty = 2 固定 ----------
    g1 = [d for d in doctors if group[d] == 1]
    hd = [c for c in shifts if sub_of[c] == 1]   # -1
    sg = [c for c in shifts if sub_of[c] != 1]   # single

    random.shuffle(g1)
    for doc in g1:
        # 休日-1 から 1 回
        for col in hd:
            if can_work(doc, col) and 'G1' not in duty[col].values():
                duty[col][doc] = 'G1'
                duty_cnt[doc] = 1
                last_work[doc] = day_of[col]
                weekly_cnt[doc][week_idx(day_of[col])] = 1
                holiday1_cnt[doc] = 1
                break
        # 平日 / -2 から 1 回
        for col in sg:
            if duty_cnt[doc] == 2:
                break
            if can_work(doc, col) and 'G1' not in duty[col].values():
                duty[col][doc] = 'G1'
                duty_cnt[doc] = 2
                last_work[doc] = day_of[col]
                weekly_cnt[doc][week_idx(day_of[col])] = 1
                break

    # --- ② Group0 Duty 1–2 回 ----------
    g0 = [d for d in doctors if group[d] == 0]
    random.shuffle(g0)

    # 1 回目を確保
    for doc in g0:
        for col in shifts:
            need = (
                (sub_of[col] == 1 and 'G0' not in duty[col].values()) or
                (sub_of[col] != 1 and not duty[col])
            )
            if need and can_work(doc, col):
                duty[col][doc] = 'G0'
                duty_cnt[doc] += 1
                last_work[doc] = day_of[col]
                weekly_cnt[doc][week_idx(day_of[col])] = 1
                if sub_of[col] == 1:
                    holiday1_cnt[doc] += 1
                break

    # 2 回目（空き枠がある列）  
    for col in shifts:
        if (sub_of[col] == 1 and 'G0' not in duty[col].values()) or \
           (sub_of[col] != 1 and not duty[col]):
            cands = [d for d in g0 if duty_cnt[d] < 2 and can_work(d, col)]
            if cands:
                doc = random.choice(cands)
                duty[col][doc] = 'G0'
                duty_cnt[doc] += 1
                last_work[doc] = day_of[col]
                weekly_cnt[doc][week_idx(day_of[col])] = 1
                if sub_of[col] == 1:
                    holiday1_cnt[doc] += 1

    # --- ③ on-call (G0) : 上限 2 回 ----------
    for col in sg:
        if 'G1' in duty[col].values():
            cands = [
                d for d in g0
                if d not in duty[col] and oncall_cnt[d] < 2 and can_work(d, col)
            ]
            if cands:
                doc = random.choice(cands)
                oncall[col] = doc
                oncall_cnt[doc] += 1
                last_work[doc] = day_of[col]

    # --- DataFrame 出力 ----------
    rows = []
    for c in shifts:
        rows.append({
            'Shift': c,
            'Duty_G0': ', '.join([d for d, g in duty[c].items() if g == 'G0']),
            'Duty_G1': ', '.join([d for d, g in duty[c].items() if g == 'G1']),
            'Oncall_G0': oncall.get(c, '')
        })
    schedule_df = pd.DataFrame(rows)

    summary_df = pd.DataFrame({
        'Group':  [group[d] for d in doctors],
        'Duty':   [duty_cnt[d] for d in doctors],
        'Oncall': [oncall_cnt[d] for d in doctors]
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
