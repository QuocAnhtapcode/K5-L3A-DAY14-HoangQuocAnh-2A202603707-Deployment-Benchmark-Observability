# Benchmark & Observability Report — Day 14

**Họ tên** Hoàng Quốc Anh  
**Ngày chạy** 25/11/2026 (GMT+7)  
**Môi trường** Ubuntu WSL2, Linux x86_64, Python 3.11.18 cho benchmark develop. WSL ghi nhận CPU AMD Ryzen 9 7945HX, 32 CPU logic và 19 GiB RAM. Client và agent cùng máy. Lượt develop đi qua loopback, lượt production đi qua mạng Docker. Mỗi replica benchmark chạy một tiến trình Uvicorn. Model mặc định là `mock-llm-small`.

## 1. SLO của agent

| SLI | Mục tiêu áp dụng | Lý do chọn |
| --- | --- | --- |
| p95 latency của `POST /ask` | ≤ 500 ms trong load test | Theo dõi độ trễ mà ít nhất 95% request thành công đạt được. Ngưỡng này giúp nhận thấy tail latency ảnh hưởng tới một phần đáng kể người dùng. |
| Error rate của `POST /ask` | ≤ 2% trong load test | Đo tỷ lệ mọi kết quả khác HTTP 200 trên tổng lượt gửi, kể cả lỗi kết nối nếu có. |
| Accuracy trên golden dataset | ≥ 85% và không regression trong CI. Bản sửa cuối CP7 phải đạt 100%. | Ngưỡng CI chặn phiên bản trả lời sai quá nhiều. So sánh theo ID phát hiện một câu đã đúng chuyển thành sai, kể cả khi accuracy tổng vẫn cao. |

p99 được báo cùng p95 để nhìn nhóm request chậm nhất. Script load test áp gate cho p95 và error rate. Nhãn PASS/FAIL trong bảng bên dưới lấy từ `slo.passed` của từng JSON.

### Exercise 7.1 — Kiểm tra metrics

Kiểm tra `/metrics` sau khi gửi một câu hỏi 40 ký tự vào `POST /ask` và gọi hai URL thử khác nhau không khớp route. Trích output thực tế:

```prometheus
# TYPE http_requests_total counter
http_requests_total{method="POST",route="/ask",status="200"} 1.0
http_requests_total{method="GET",route="unmatched",status="404"} 2.0
# TYPE agent_in_flight_requests gauge
agent_in_flight_requests 0.0
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_count{method="POST",route="/ask"} 1.0
# TYPE agent_question_length_chars histogram
agent_question_length_chars_bucket{le="20.0"} 0.0
agent_question_length_chars_bucket{le="50.0"} 1.0
agent_question_length_chars_count 1.0
agent_question_length_chars_sum 40.0
```

- **Counter** `http_requests_total` đếm request tích lũy theo method, route và status.
- **Gauge** `agent_in_flight_requests` tăng khi request đến và giảm khi hoàn tất. Giá trị `0.0` là trạng thái lúc đọc metrics.
- **Histogram** `http_request_duration_seconds` nhóm các quan sát latency vào bucket. Histogram mới `agent_question_length_chars` quan sát độ dài ngay khi `/ask` nhận body hợp lệ, trước bước retrieval và gọi model. Bucket `le="50.0"` đã nhận câu hỏi 40 ký tự.

`observe_requests` dùng template `route.path` để nhiều request cùng route dùng chung series. Hai URL thử khác nhau đều tăng cùng counter `route="unmatched",status="404"`. Nếu dùng raw URL làm label, mỗi đường dẫn riêng có thể tạo thêm time series cho counter và các bucket histogram. Hậu quả là bộ nhớ Prometheus và chi phí truy vấn tăng theo số URL khác nhau. [Mã đo metrics](06-lab-complete/benchmark-observability/develop/app.py)

## 2. Benchmark hiệu năng (develop, `load_test.py`)

Mỗi phép thử gửi **300 request được tính vào kết quả** và có một request warm-up riêng. RPS là tổng số lượt kết thúc chia thời gian đo của client. Các percentile latency và average chỉ tính request HTTP 200. Trong toàn bộ phép thử bên dưới, lỗi ghi nhận đều có HTTP 503. Giữ `MOCK_ERROR_RATE=0.01`, latency cơ bản 120 ms, jitter tối đa 60 ms và retrieval cơ bản 20 ms. Tất cả phép đo hiệu năng dùng v2 **trước khi bổ sung knowledge base**.

### 2.1 Concurrency vs latency (agent v2)

Cố định `MOCK_SLOW_RATE=0.03`. Chạy lần lượt 1, 10, 50 và 100 worker của client.

```bash
for concurrency in 1 10 50 100; do
  python load_test.py --url http://localhost:8002 --requests 300 \
    --concurrency "$concurrency" --out "reports/load_v2_c${concurrency}.json"
done
```

| Concurrency | Thời gian (s) | RPS | p50 (ms) | p95 (ms) | p99 (ms) | HTTP 503 | SLO | Báo cáo |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 1 | 74,72 | 4,02 | 186,5 | 218,0 | 1703,2 | 2/300 (0,67%) | PASS | [JSON](reports/load_v2_c1.json) |
| 10 | 7,67 | 39,13 | 187,2 | 216,5 | 1691,2 | 3/300 (1,00%) | PASS | [JSON](reports/load_v2_c10.json) |
| 50 | 2,70 | 110,92 | 196,1 | 261,2 | 1695,9 | 0/300 (0,00%) | PASS | [JSON](reports/load_v2_c50.json) |
| 100 | 2,15 | 139,78 | 201,9 | 306,6 | 1695,9 | 7/300 (2,33%) | FAIL | [JSON](reports/load_v2_c100.json) |

Từ concurrency 1 lên 10, throughput tăng khoảng **9,73 lần**. Từ 50 lên 100, số worker client tăng gấp đôi nhưng throughput chỉ tăng từ **110,92** lên **139,78 RPS**, khoảng **1,26 lần**. Cùng bước đó, p95 tăng **45,4 ms**. Trong một lần đo ngắn với 300 request, chi phí mở hoặc dùng lại kết nối, điều phối đồng thời của client/server và các request chậm đều có thể làm throughput tăng dưới tuyến tính. Các số đo này chưa đủ để quy mức tăng chậm cho riêng CPU hoặc một bước xử lý cụ thể.

Concurrency 100 **FAIL vì error rate 2,33%**, dù p95 **306,6 ms** vẫn đạt mục tiêu. Cả 7 lượt lỗi trong lần ấy đều là HTTP 503 do app trả. Với lỗi mock ngẫu nhiên, một lần đo không đủ để kết luận concurrency gây ra lỗi. Kết quả ở 50 là mức concurrency cao nhất trong bốn mức đo đạt cả hai SLO.

### 2.2 Tail latency

Restart v2 giữa ba lần đo để đặt `MOCK_SLOW_RATE` lần lượt thành `0`, `0.03`, `0.1`. Giữ 300 request, concurrency **20** và cùng các thiết lập khác. Request bị đánh dấu chậm được cộng **1.500 ms** vào thời gian mock LLM.

| `MOCK_SLOW_RATE` | Avg (ms) | p95 (ms) | p99 (ms) | RPS | HTTP 503 | SLO | Báo cáo |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 0 | 187,0 | 213,7 | 223,0 | 103,73 | 4/300 (1,33%) | PASS | [JSON](reports/load_v2_slow_0.json) |
| 0.03 | 253,0 | 222,1 | 1712,5 | 58,81 | 0/300 (0,00%) | PASS | [JSON](reports/load_v2_slow_0.03.json) |
| 0.1 | 318,2 | 1678,4 | 1711,1 | 51,31 | 5/300 (1,67%) | FAIL | [JSON](reports/load_v2_slow_0.1.json) |

Từ slow rate 0 lên 0,03, avg tăng **66,0 ms** và p95 tăng **8,4 ms**, trong khi p99 tăng **1.489,5 ms** lên **1.712,5 ms**. Một nhóm nhỏ request chậm đã tác động mạnh tới p99. Ở slow rate 0,1, p95 tăng lên **1.678,4 ms** và làm SLO latency thất bại dù avg vẫn chỉ **318,2 ms**. Khi nhóm request chậm vượt khoảng 5% số request thành công, p95 bắt đầu rơi vào nhóm đó. Average chia phần trễ thêm trên tất cả request nên có thể che mức chậm mà nhóm người dùng ấy gặp phải.

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Chạy v1 trên cổng 8001 và v2 trên cổng 8002. Ba lượt eval dùng cùng 23 câu và đặt `MOCK_ERROR_RATE=0`, `MOCK_SLOW_RATE=0` để so chất lượng và latency cơ bản trong cùng điều kiện. Mỗi câu đạt nếu câu trả lời chứa ít nhất 60% bộ keyword kỳ vọng. Baseline v2 so với v1 dùng ngưỡng mặc định 80%. Bản sửa cuối so với v2 ban đầu dùng ngưỡng 100%.

| Chỉ số | Agent v1 | Agent v2 | Agent v2 sau sửa KB |
| --- | ---: | ---: | ---: |
| Accuracy | 1/23 (4,35%) | 21/23 (91,30%) | 23/23 (100,00%) |
| Avg tokens / câu | 58,0 | 109,3 | 114,2 |
| Tổng chi phí mock (USD) | 0.000417 | 0.000764 | 0.000795 |
| Chi phí / câu đúng (USD) | 0.00041715 | 0.00003639 | 0.00003458 |
| p50 / p95 (ms) | 170,2 / 188,3 | 196,1 / 215,1 | 182,8 / 217,4 |
| Lỗi HTTP hoặc kết nối | 0 | 0 | 0 |
| Ngưỡng quality gate | 80% | 80% | 100% |
| Quality gate | FAIL | PASS | PASS |
| Bằng chứng | [JSON](reports/eval_v1.json) | [JSON](reports/eval_v2.json) | [JSON](reports/eval_v2_fixed.json) |

**Hai câu v2 trả lời sai ban đầu**

| ID | Keyword thiếu trong lần chạy gốc | Thay đổi |
| --- | --- | --- |
| `security-02` | `token`, `hết hạn`, `chữ ký` | Thêm tài liệu truy xuất theo `jwt` và giải thích JWT có claim, chữ ký và hạn dùng. |
| `scaling-03` | `cpu`, `memory`, `custom metrics` | Thêm tài liệu truy xuất theo `hpa` hoặc `horizontal pod autoscaler` và giải thích những metric HPA dùng để điều chỉnh số pod. |

Hai câu ban đầu nhận câu trả lời chung. Sau khi thêm mục vào `develop/utils/mock_llm.py` và restart v2, cả hai đạt và toàn bộ câu đã đúng ở baseline vẫn đúng. Golden dataset giữ nguyên. [Knowledge base](06-lab-complete/benchmark-observability/develop/utils/mock_llm.py) · [Bản eval đã sửa](reports/eval_v2_fixed.json)

**Trade-off quan sát được.** Retrieval giúp v2 tăng từ **1/23** lên **21/23**, rồi đạt **23/23** khi bổ sung hai tài liệu. V2 trước sửa dùng **109,3 token/câu**, cao hơn **58,0** của v1. p95 eval tăng từ **188,3 ms** của v1 lên **215,1 ms** ở v2 và **217,4 ms** ở v2 đã sửa, phù hợp với việc thêm retrieval và context. Tổng cost của v2 đã sửa tăng từ **0.000417 USD** lên **0.000795 USD** so với v1, nhưng **cost trên mỗi câu đúng giảm khoảng 12,1 lần**. Chỉ số trên mỗi câu đúng phản ánh chi phí để đạt một kết quả sử dụng được. Giá trong báo cáo là số do mock model tính.

### Exercise 7.3 — Quality gate và cách chặn merge

Chạy gate cuối từ thư mục `develop` sau khi restart v2:

```bash
python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 \
  --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json
```

Kết quả **23/23**, **0 regression**, exit code **0**. Phép thử đối chứng chạy **v1** với `--min-accuracy 0.85 --baseline reports/eval_v2.json` cho **1/23**, **20 regression**, exit code **1**.

Trong CI cho pull request, job cài requirements, khởi động agent đang xét, đợi `/health` trả 200 rồi chạy `run_eval.py --min-accuracy 0.85 --baseline "$BASELINE_JSON"`. Lấy baseline đã duyệt từ nhánh đích để mốc so sánh ổn định. Script trả **1** khi accuracy dưới ngưỡng hoặc có ID từng đạt chuyển thành không đạt. GitHub Actions đánh dấu job thất bại khi lệnh trả mã khác 0. Đặt job eval là required status check trên nhánh đích để GitHub chặn merge khi gate thất bại. [Script quality gate](06-lab-complete/benchmark-observability/develop/eval/run_eval.py)

## 4. Observability stack (production)

Chạy Docker Compose với project `k5-day14-observability` trên WSL2. Locust gọi agent qua Nginx. Prometheus scrape từng replica trực tiếp và Grafana đọc dữ liệu từ Prometheus. Agent gửi trace OpenTelemetry qua OTLP/HTTP sang Jaeger. Bài 7.4 dùng 30 user, ramp 5 user/s và 2 replica. Bài 7.6 giữ tải này khi thay đổi tham số. Phép benchmark 100 user ở mục 5 chạy riêng.

### 4.1 Screenshot

- [Grafana với tải 30 user](screenshots/grafana-dashboard.png)
- [Jaeger với trace chậm và span LLM](screenshots/jaeger-slow-trace.png)
- [Jaeger với trace HTTP 503 và event `exception`](screenshots/jaeger-error-trace.png)
- [Trang Alerts của Prometheus khi hai alert cùng firing](screenshots/prometheus-alert-firing.png)

**Exercise 7.4.** Ảnh Grafana chụp lúc Locust đang chạy 30 user. Các ô Golden Signals tại khung hình hiển thị **18,7 req/s**, **1,36%** lỗi 5xx, **p95 292,04 ms** và **5** request in-flight, với **2** instance up. Các ô tự làm mới sau mỗi 5 giây. Để so latency, instance và token trên cùng thời điểm, lấy một snapshot Prometheus khác lúc **16:06:40 GMT+7** sau khi đã ramp đủ 30 user:

| Quan sát | Giá trị ở snapshot | Diễn giải |
| --- | --- | --- |
| p95, avg và p99 của `/ask` | 289,56 ms / 217,65 ms / 1.759,29 ms | p95 cao hơn avg 71,91 ms và p99 cách p95 gần 1,47 giây. Avg chia đều ảnh hưởng của các lượt chậm. p95 và p99 được nội suy từ histogram Prometheus cửa sổ 1 phút, còn avg lấy tốc độ tăng tổng latency chia tốc độ tăng số lượt. |
| Traffic theo instance | `172.21.0.4:8000` 9,13 req/s và `172.21.0.8:8000` 9,25 req/s | Khoảng 49,65% và 50,35% trên tổng 18,38 req/s, tức tải chia gần đều trong cửa sổ quan sát. |
| Tokens/s | Input 1.337,43 và output 711,44 token/s | Input gấp khoảng 1,88 lần output. Input của v2 tính system prompt, câu hỏi và context truy xuất. Output chỉ tính văn bản trả lời. |
| `GET http://localhost:8080/metrics` | HTTP 404 | Nginx có `location = /metrics { return 404; }`. Prometheus scrape cổng `8000` của từng agent trong mạng Compose. Quy tắc giữ các metric như route, lưu lượng và instance khỏi cổng vào ứng dụng. |

[Dashboard provisioned](06-lab-complete/benchmark-observability/production/grafana/dashboards/agent-overview.json) · [Cấu hình Nginx](06-lab-complete/benchmark-observability/production/nginx/nginx.conf)

### 4.2 Thí nghiệm alert và chaos

`promtool test rules /p/alert_rules_test.yml` trả **SUCCESS**. Khi thử trực tiếp, giữ tải 30 user trên 2 replica. Mốc bắt đầu là lúc chạy Compose với cấu hình mới hoặc gửi `docker pause`. Thời gian ở bảng là lần đầu API `/api/v1/alerts` trả `firing`, được kiểm khoảng mỗi 2 giây. Thời gian này gồm độ trễ của container, scrape, cửa sổ tính `rate` và thời gian `for` theo từng rule. Các giờ dưới đây dùng GMT+7. [Cấu hình alert](06-lab-complete/benchmark-observability/production/prometheus/alert_rules.yml) · [Prometheus HTTP API](https://prometheus.io/docs/prometheus/3.16/querying/api/)

| Thí nghiệm | Thay đổi và ngưỡng | Alert | Thời gian quan sát | Panel và số liệu |
| --- | --- | --- | --- | --- |
| A | `MOCK_ERROR_RATE=0.20`. Tỷ lệ 5xx `/ask` vượt 5% trong 1 phút. | `AgentHighErrorRate` | **92,5 giây**, thấy lúc **16:09:13** | Error rate. Tại đánh giá đầu tiên báo firing, giá trị rule là **21,91%**. |
| B | `MOCK_SLOW_RATE=0.30`. p95 `/ask` vượt 1 giây trong 2 phút. | `AgentHighLatencyP95` | **146,5 giây**, thấy lúc **16:10:07** | Latency p95 và biểu đồ percentiles. Khi cả hai cùng firing, p95 khoảng **1,895 s**, p99 **1,979 s**, avg **0,566 s**. |
| C | `LLM_MODEL=mock-llm-large`, đồng thời đưa tỷ lệ lỗi/chậm về mặc định. Chi phí ước tính vượt 2 USD/giờ trong 2 phút. | `LLMCostBurnRateHigh` | **148,4 giây**, từ **16:11:00** đến **16:13:29** | Cost burn rate. Alert đã hết ngay trước khi đổi model, lúc panel báo **1,73 USD/giờ**. Lần firing mới báo **15,37 USD/giờ** theo giá trị của rule. |
| D | Khôi phục model small rồi `docker pause` một replica. Chờ `up == 0` trong 30 giây. | `AgentInstanceDown` | **42,1 giây**, từ **16:14:41** đến **16:15:23** | Instances up giảm **2 → 1**. Target `172.21.0.8:8000` vẫn được phát hiện nhưng scrape thất bại. Log Nginx có **27 HTTP 504** và **0 HTTP 502** trong lúc đo. |
| E | `AGENT_VERSION=v1` với model small. | Các rule hiện tại không đo chất lượng câu trả lời. | Quan sát **16:15:25–16:16:38**. Không phát sinh alert mới. | Version hiển thị **2 replica v1**. Input/output còn **677,47 / 369,73 token/s**. p95 khoảng **198 ms**, error rate khoảng **0,98%**. |

A và B áp dụng cùng lúc từ **16:07:41** để duy trì hai alert trên cùng ảnh. Giá trị snapshot lúc **16:10:07** là **15,27 req/s**, **17,86%** lỗi 5xx và **p95 1,895 s**. Prometheus ghi A vào `pending` lúc **16:08:07**, B lúc **16:08:02**. B vào pending sớm hơn nhưng firing muộn hơn vì phải giữ ngưỡng 2 phút. Grafana cho thấy p95 tăng từ khoảng **0,290 s** ở baseline lên **1,895 s**, trong khi avg tăng từ **0,218 s** lên **0,566 s**. Vì thay đổi hai biến cùng lượt, đây là mức thay đổi kết hợp quan sát được. `LLMCostBurnRateHigh` cũng hiện trong ảnh vì đã firing dưới tải trước hai thay đổi này.

Ở C, Prometheus ghi một lần `pending` mới từ **16:11:22** sau khi đổi sang model large. Snapshot kế tiếp lúc **16:14:19** ghi **21,37 USD/giờ** và p95 span LLM khoảng **496,22 ms** với model large. Baseline small ở snapshot 30 user là **197,03 ms**. Panel chi phí ước tính tốc độ theo cửa sổ trượt 5 phút. Mức tăng quan sát giữa hai snapshot phản ánh cả giá mock, số token và lưu lượng thành công trong từng cửa sổ.

Ở D, container bị pause vẫn còn trong DNS nên Prometheus giữ target và biểu diễn scrape thất bại bằng `up=0`. Dừng container có thể khiến target biến mất khi khám phá lại DNS. Tỷ lệ 5xx do agent ghi tại snapshot D là khoảng **1,00%**. Nginx sinh các mã 504 khi chờ upstream hết hạn, nằm ngoài số lỗi mà agent có thể ghi nhận. Sau khi thu số liệu, chạy `docker unpause` và đợi cả hai target lên lại.

Ở E, khi hỏi “Prometheus thu thập metric như thế nào?”, v1 trả HTTP 200 với câu chung chung “Câu hỏi hay! Điều này phụ thuộc vào kiến trúc hệ thống và yêu cầu cụ thể của bạn.” Truy vấn tức thời `agent_retrieval_total{job="agent"}` không có series ở v1, dù panel dùng cửa sổ 5 phút vẫn còn tính các mẫu v2 cũ. `LLMCostBurnRateHigh` lúc đó vẫn giữ mốc pending từ **16:11:22** ở C vì cửa sổ chi phí còn chứa lượt dùng model large. Alert kéo dài này không phản ánh nội dung câu trả lời. Stack trở lại v2 và model small sau phép thử.

Để phát hiện sự cố chất lượng trước khi merge hoặc phát hành, chạy golden eval với baseline và đặt quality gate như mục 3. Bài eval cho thấy v1 chỉ đạt **1/23**, dù mẫu production ở trên trả 200 và p95 thấp. Sau phát hành, có thể chạy định kỳ các câu canary có đáp án kỳ vọng theo nhóm chủ đề, ghi tỷ lệ đạt theo `agent_version` và cảnh báo khi thấp hơn baseline đủ lâu. Khi canary báo regression, xem câu sai và trace của phiên bản mới trước khi tiếp tục rollout.

Để lặp lại A và B từ thư mục `production`, chạy lệnh dưới đây khi Locust đang phát 30 user, ramp 5 user/s. Chỉ dùng biến môi trường của lệnh để cấu hình container.

```bash
MOCK_ERROR_RATE=0.20 MOCK_SLOW_RATE=0.30 \
  docker compose -p k5-day14-observability up -d --no-deps --scale agent=2 agent
curl -fsS http://localhost:9090/api/v1/alerts
```

### 4.3 Debug bằng trace

**Trace chậm `a52adddfd2c72bb4b288558916f434dc`.** `POST /ask` trả HTTP 200 sau **1.720,769 ms**. Trong `agent.run`, span `chat mock-llm-small` mất **1.696,907 ms**, khoảng **98,6%** thời gian từ đầu tới cuối. `agent.retrieve_context` mất **22,293 ms**. Timeline Jaeger đặt phần lớn thời gian ở lần gọi model. Log ghi `llm_ms=1698.0` từ độ trễ mô phỏng do mock LLM trả về, làm tròn đến một chữ số thập phân.

```json
{"ts":"2026-11-25T09:01:49.459898+00:00","level":"INFO","service":"observable-agent","instance":"a574567f0efd","event":"llm_call","trace_id":"a52adddfd2c72bb4b288558916f434dc","span_id":"39bca703172d0bb2","request_id":"51c94968afdf","model":"mock-llm-small","agent_version":"v2","input_tokens":95,"output_tokens":55,"cost_usd":4.7e-05,"llm_ms":1698.0}
```

**Trace lỗi `ad62b2c7b5a5f598e11fa45384ea7088`.** `POST /ask` trả **HTTP 503** sau **844,926 ms**. Span `chat mock-llm-small` kéo dài **823,416 ms** và có event `exception`, với kiểu `utils.mock_llm.LLMError` và thông điệp `Upstream LLM error: 503 Service Unavailable (mock)`. Span retrieval hoàn thành sau **18,945 ms**. Cây span chỉ ra lỗi phát sinh ở bước gọi model. `span_id` trong log trỏ tới span gốc `POST /ask`. Event nằm ở span con `chat`. Cả hai được đối chiếu bằng `trace_id` của toàn lượt gọi.

```json
{"ts":"2026-11-25T09:02:08.302609+00:00","level":"INFO","service":"observable-agent","instance":"a574567f0efd","event":"llm_error","trace_id":"ad62b2c7b5a5f598e11fa45384ea7088","span_id":"0c207cb785a542ab","request_id":"cc89579084c4","error":"Upstream LLM error: 503 Service Unavailable (mock)"}
```

Tìm theo ID trong log bằng `docker compose -p k5-day14-observability logs --no-color agent | grep -F "$TRACE_ID"`, rồi mở cùng ID trong Jaeger. Đối chiếu thời lượng và event từ API trace của Jaeger. [Jaeger Query API](https://www.jaegertracing.io/docs/2.23/architecture/apis/)

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Giữ agent v2, `mock-llm-small`, `MOCK_ERROR_RATE=0.01`, `MOCK_SLOW_RATE=0.03` và `MOCK_LATENCY_MS=120`. Mỗi lượt dùng 100 user, ramp 10 user/s, `-t 2m` tính từ lúc bắt đầu ramp. Mỗi user nghỉ ngẫu nhiên 0,5–2 giây giữa các tác vụ. Trước mỗi lượt, xác nhận Prometheus đã scrape được đúng số replica và `/health` qua Nginx trả 200. Chạy từ thư mục `production`:

```bash
docker compose -p k5-day14-observability up -d --build --scale agent=1
# Chờ Prometheus thấy 1 target UP trước khi chạy Locust.
docker compose -p k5-day14-observability run --rm --no-deps locust \
  -f /mnt/locust/locustfile.py --host http://nginx \
  --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_1
docker compose -p k5-day14-observability up -d --no-deps --scale agent=3 agent
# Chờ Prometheus thấy 3 target UP trước khi chạy Locust.
docker compose -p k5-day14-observability run --rm --no-deps locust \
  -f /mnt/locust/locustfile.py --host http://nginx \
  --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_3
```

Trên Docker Compose v5.1.4, `run` có thể khởi động lại dependency theo `deploy.replicas: 2`. Dùng `run --no-deps` để giữ đúng số agent đã xác nhận trước từng lượt. Lượt 1 chạy khoảng **16:01:17–16:03:17**, lượt 3 khoảng **16:03:36–16:05:36** (GMT+7). Hai lượt đo đủ thời lượng và Locust trả exit code 1 vì có request lỗi.

| Replica | Request `/ask` | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures | CSV |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 7.202 | 60,31 | 190 | 220 | 1.700 | 62/7.202 (0,86%) | [scale_1_stats.csv](benchmark-results/scale_1_stats.csv) |
| 3 | 7.185 | 60,23 | 190 | 220 | 1.700 | 82/7.185 (1,14%) | [scale_3_stats.csv](benchmark-results/scale_3_stats.csv) |

Bảng lấy dòng `POST /ask` trong CSV vì dòng `Aggregated` cộng thêm `/health`. Locust ghi percentile theo bucket và tính trên các request đã ghi nhận, bao gồm failures. Các percentile của `load_test.py` ở mục 2 chỉ tính HTTP 200. Cả hai lần có p95 và tỷ lệ lỗi dưới các ngưỡng tham chiếu trong mục 1. File failures ghi 62 và 82 lần HTTP 503, phù hợp với lỗi do mock LLM. Mức chênh **0,28 điểm phần trăm** giữa hai lượt chạy đơn lẻ chưa đủ để quy mức lỗi tăng cho thao tác scale. [Cách Locust tổng hợp latency](https://docs.locust.io/en/2.47.0/_modules/locust/stats.html) · [Lỗi scale 1](benchmark-results/scale_1_failures.csv) · [Lỗi scale 3](benchmark-results/scale_3_failures.csv)

Khi tăng từ một lên ba replica, p95 vẫn **220 ms**, p99 vẫn **1.700 ms** và RPS dao động khoảng **−0,13%**. Benchmark này dùng số user cố định và có thời gian nghỉ, nên số request được tạo ra cũng bị giới hạn bởi nhịp của Locust. Mock LLM chờ bất đồng bộ trong mỗi request. Trace ở mục 4.3 cho thấy một lượt chậm dành **98,6%** thời gian trong span model. Tăng replica cho phép xử lý song song nhiều request hơn khi tải đủ lớn, nhưng mỗi lần gọi model vẫn chịu cùng phân phối thời gian chờ. Kết quả ở 100 user chưa xác định được công suất tối đa của từng cấu hình. Muốn xác định ngưỡng bão hòa, cần đo ở nhiều mức tải cùng với in-flight, CPU từng replica và thời gian chờ ở upstream.

## 6. Kết luận

1. Ở CP7, concurrency 50 là mức cao nhất trong các mức đo đạt cả p95 và error rate. Ở CP8, thêm replica với cùng tải 100 user ít thay đổi throughput và percentile. Hai phép đo dùng cách phát tải khác nhau, nên cần ghi rõ workload khi dùng kết quả để quyết định scale.
2. Một nhóm nhỏ request chậm có thể làm p99 tăng khoảng 1,49 giây khi avg gần 253 ms. Metrics cho biết thời điểm độ trễ tăng, còn trace của lượt gọi cụ thể xác định bao nhiêu thời gian nằm ở model và bao nhiêu nằm ở retrieval.
3. Eval trên cùng dataset phát hiện chất lượng giảm khi đổi phiên bản. Bổ sung hai tài liệu giúp v2 đạt 100% và giảm chi phí trên mỗi câu đúng so với baseline v2.

Nếu triển khai agent thật, thêm phép kiểm tra tổng hợp đi qua Nginx và tỷ lệ lỗi/timeout đo từ tầng proxy để thấy lỗi người dùng gặp trước khi agent trả response. Giữ thêm xu hướng eval theo phiên bản và gắn ngưỡng cho accuracy theo từng nhóm câu hỏi. Bộ số liệu này phản ánh mock model, một máy local và một lần đo cho mỗi cấu hình.
