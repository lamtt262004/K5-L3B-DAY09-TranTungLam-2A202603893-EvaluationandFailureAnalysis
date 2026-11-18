# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (judge replay, n = 100 mỗi tiêu chí, FAIL là lớp dương).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **Đạt** (exit 0) | **dùng** (non-blocking, chỉ cho giọng văn) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **Không đạt** (κ, R dưới sàn; exit 1) | **loại** |

Ma trận nhầm lẫn:
- tone_ok: `tp=24, fp=4, fn=6, tn=66`. Tỉ lệ FAIL judge thấy 0.28 → Rogan-Gladen hiệu chỉnh 0.30 (= tỉ lệ FAIL thật của người: 30/100).
- helpful: `tp=9, fp=0, fn=21, tn=70`. Judge chỉ báo FAIL 9%, Rogan-Gladen hiệu chỉnh lên 0.30: **judge bỏ sót 21/30 câu trả lời người chấm FAIL** (recall 0.3).

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha, anh check giúp em." | PASS | FAIL | Người | Xưng "em" + "nha" quá suồng sã với khách. Rubric: thêm quy tắc xưng hô "LogiX – anh/chị", liệt kê từ cấm ("nha", "nhé", "bạn ơi") kèm ví dụ FAIL. |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ ... rồi nha." | PASS | FAIL | Người | Judge coi "bạn ơi" là thân thiện. Cùng sửa như #1. |
| 3 | H-029 "Dạ, vui lòng chờ. ... không cần nhắn thêm." | PASS | FAIL | Người | Judge chỉ thấy từ lịch sự ở đầu câu. Rubric: chấm **cả câu**; câu ra lệnh/gạt khách ("không cần nhắn thêm") là FAIL dù mở đầu bằng "Dạ". |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì thiếu xin lỗi. Rubric: xin lỗi **không bắt buộc** khi kết quả có lợi cho khách; ngắn + đủ ý = PASS. |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ cho đơn O-202 ạ." | FAIL | PASS | Người | Từ "refund" được CS LogiX cho phép. Rubric: thêm whitelist thuật ngữ ("refund", "ship", "COD"). |
| 6 | H-063 "Dạ, LogiX đã hoàn 20.000đ và sẽ hoàn thêm cho anh/chị ạ." | PASS | FAIL | Người | Hứa "sẽ hoàn thêm" ngoài kết quả hệ thống. Đây **không phải lỗi giọng văn** mà là lỗi nội dung: tách khỏi tone, giao cho grader code (`ReplyMatchesLedger` / kiểm tra cam kết ngoài ledger). |
| 7 | HL-015 "Dạ, LogiX đã hoàn 55.000đ cho đơn O-112 ạ." (ledger 40.000đ) | PASS | FAIL | Người | `helpful` judge không có ledger nên không thể biết số tiền sai. Đây là FM-08: chấm bằng code `reply_matches_ledger`, không bao giờ bằng judge. |
| 8 | HL-016 xin lỗi dài 3 câu + "đã hoàn 55.000đ cho đơn O-404" (ledger 35.000đ) | PASS | FAIL | Người | Câu dài, nhiều lời xin lỗi được judge thưởng (verbosity/sycophancy bias) dù sai tiền. Rubric: độ dài và xin lỗi không cộng điểm; câu đúng tiền là điều kiện cần. |

## Kết luận
- **tone_ok dùng được** (κ 0.757, P 0.857, R 0.8 đều ≥ sàn), nhưng chỉ ở vai trò non-blocking cho giọng văn. 10 bất đồng = 6 fn (judge dễ dãi: xưng hô suồng sã, giọng ra lệnh, hứa ngoài hệ thống) + 4 fp (judge khắt khe: đòi xin lỗi, cấm từ "refund") → sửa rubric như bảng trên rồi calibrate lại trên nhãn mới.
- **helpful bị loại khỏi gate**: κ = 0.375 < 0.6 và recall = 0.3 < 0.7 (`fn=21`). Precision 1.0 không cứu được: judge gần như luôn nói PASS nên khi nó nói FAIL thì đúng, nhưng nó bỏ qua 70% câu trả lời xấu. Các ca bỏ sót chủ yếu là **sai số tiền** (HL-015, 016, 020, 035...) mà judge không có ledger để so. Dùng judge này làm gate thì một regression FM-08 sẽ lọt qua với tỉ lệ fail quan sát thấp hơn thật ~3 lần (0.09 so với 0.30 sau Rogan-Gladen). Phần "đúng tiền" của helpful đã thuộc grader code `reply_matches_ledger`.
- Quy tắc giữ: tiền và approval luôn chấm bằng code; judge chỉ cho tiêu chí chủ quan đã qua calibration.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai (không có held-out); rubric sửa ở trên chưa được đo lại; cần nhãn người mới, có người chấm thứ hai để đo inter-annotator κ trước khi tin con số 0.757.
