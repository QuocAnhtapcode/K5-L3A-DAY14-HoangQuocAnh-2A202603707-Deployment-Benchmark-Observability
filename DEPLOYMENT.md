# Thông tin triển khai — CP6

## URL public

[https://day14-agent-cmt0.onrender.com](https://day14-agent-cmt0.onrender.com)

## Nền tảng và mã nguồn

- Agent Part 6 chạy trên **Render Free** tại Singapore. Blueprint `day14-lab` tạo web service `day14-agent` và Key Value `day14-redis` trong cùng region. Key Value chỉ nhận kết nối nội bộ.
- Render lấy commit `2ff4443` từ nhánh `main`. Blueprint Path là `06-lab-complete/render.yaml`. Cấu hình này build [Dockerfile](06-lab-complete/Dockerfile) và chạy [agent Part 6](06-lab-complete/app/main.py) trong thư mục `06-lab-complete/`. [Blueprint Render](06-lab-complete/render.yaml)
- Render ghi nhận deployment **Deploy succeeded | Live** lúc **15:34:18 ngày 25/11/2026 (GMT+7)**. [Chi tiết deployment](https://dashboard.render.com/web/srv-dc39r2cipgvc7382c7rg/deploys/dep-dc39r2kipgvc7382c8rg)
- Lần thử Railway trước đó dừng ở bước tạo service vì workspace Trial bị hạn chế. Sau đó bài lab được triển khai trên Render Free.

## Cấu hình service agent

| Biến | Cách cấu hình trên Render |
| --- | --- |
| `PORT` | Dùng cổng Render cấp. Dockerfile bind `0.0.0.0` và đọc biến này khi khởi động. |
| `REDIS_URL` | Blueprint lấy `connectionString` của `day14-redis` qua `fromService` để kết nối nội bộ. |
| `AGENT_API_KEY` | Render sinh giá trị cho service agent bằng `generateValue`. |
| `ENVIRONMENT` | `production` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.00` |

## Lệnh kiểm tra cloud

Dùng các lệnh Bash dưới đây để chạy lại bốn phép thử. Lấy giá trị `AGENT_API_KEY` từ mục **Environment** của service `day14-agent` trên Render, rồi nhập kín khi Bash yêu cầu.

```bash
BASE='https://day14-agent-cmt0.onrender.com'
read -rsp 'API key: ' AGENT_API_KEY
printf '\n'

curl -sS -o /dev/null -w 'GET /health → %{http_code}\n' "$BASE/health"
curl -sS -o /dev/null -w 'GET /ready → %{http_code}\n' "$BASE/ready"
curl -sS -o /dev/null -w 'POST /ask thiếu key → %{http_code}\n' \
  -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'
curl -sS -o /dev/null -w 'POST /ask có key → %{http_code}\n' \
  -X POST "$BASE/ask" -H 'Content-Type: application/json' \
  -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'
unset AGENT_API_KEY
```

## Kết quả đã xác minh

Bốn phép thử trên URL public được thực hiện lúc **15:37:35 ngày 25/11/2026 (GMT+7)** bằng PowerShell.

| Phép thử | HTTP kỳ vọng | HTTP thực tế | Quan sát |
| --- | ---: | ---: | --- |
| `GET /health` | 200 | **200** | Agent đang phản hồi trên URL public. |
| `GET /ready` | 200 | **200** | Agent kết nối được Key Value và sẵn sàng nhận request. |
| `POST /ask` thiếu `X-API-Key` | 401 | **401** | Endpoint yêu cầu xác thực. |
| `POST /ask` với key hợp lệ | 200 | **200** | Response có câu trả lời từ mock LLM. |

## Ảnh minh chứng

- [dashboard.png](screenshots/dashboard.png) — Blueprint đã tạo `day14-agent` và `day14-redis`.
- [running.png](screenshots/running.png) — Deployment `2ff4443` ở trạng thái **Live**.
- [test.png](screenshots/test.png) — Bốn mã HTTP thực tế và URL đã kiểm tra. Ảnh không chứa giá trị API key.
