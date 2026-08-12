# Northstar RAG Evaluation UI

Chạy local từ thư mục gốc của project:

```powershell
python -m pip install -r requirements.txt
streamlit run demo-ui/app.py
```

Mở URL Streamlit được in trong terminal. Dashboard mặc định đọc snapshot từ
`artifacts/benchmark_results.json` và `artifacts/actual_answers.json`; nút
`Làm mới dữ liệu` chỉ đọc lại hai file này, không chạy lại benchmark 20 câu.

Để bật live RAG, copy `.env.example` thành `.env` và điền
`OPENROUTER_API_KEY`. API key không được hiển thị trong UI. Nếu chưa có cấu
hình hoặc mạng/API lỗi, các câu hỏi có sẵn trong benchmark sẽ dùng artifact
fallback và hiển thị trạng thái rõ ràng.
