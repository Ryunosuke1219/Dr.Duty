
# Doctor Duty Scheduler

Streamlit app to generate balanced duty & on-call schedules from a CSV.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Sample
Upload `sample/availability_sample.csv` to try.

* `utils/loose_scheduler.py` – 週1回／-1 月1回・連続4日間隔の“緩い”割付ロジック

