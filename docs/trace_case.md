# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Mẫu điền (mỗi bạn 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | `<shape> <claim>@<seed>`, run ID trong report |
| Triệu chứng | Grader nào fail; reply nói gì so với ledger ghi gì |
| Bước sai đầu tiên | Số thứ tự event + kind + trích payload (≤1 dòng) |
| Vì sao là bước *đầu tiên* | Các bước trước đó đúng ra sao (dẫn số event) |
| Failure mode | Mã FM-xx trong `docs/failure_taxonomy.md` |
| Đối chứng | Cùng claim@seed trên shape `fixed`: bước đó khác thế nào |
| Regression test | Tên test + grader/assert nào bắt được; đã thấy fail trên variant lỗi và pass trên `fixed` chưa |
| Chưa chắc chắn | Điều trace không chứng minh được |

## Case đã điền: `seeded_regression C-002@11`

```
== seeded_regression C-002@11  lab8 grader: FAIL (status resolved != denied)
   1 handoff            {"claim_id": "C-002", "contract_version": "handoff.v1", "facts": {}, ...}
   2 investigator       {"lookup_order": {"visible": true, "reason": "ok", "order": {"order_id": "O-202", ... "delay_days": 2 ...
   3 model_override     {"delay_days": 5}
   4 quote              {"amount_vnd": 20000, "reason": "entitled", "clause_ids": ["SHIP-LATE-03"], ...}
   5 refund_history     {"refund_count": 0, "refunded_vnd": 0}
   6 reply              {"text": "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202."}
  refund_vnd     ledger=20000    label=0   <-- differs
  refund_count   ledger=1        label=0   <-- differs
  status         result=resolved label=denied
```

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, report `E-seeded_regression-20261118-ac23` (eval set `ac239cf575a5`), row `E-002@11` |
| Triệu chứng | `pass_rate`, `refund_amount_correct`, `db_fact_kept`, `proposal_correct` fail. Reply "đã hoàn 20.000đ cho đơn O-202"; ledger cũng ghi 20.000đ (reply khớp ledger, nên `reply_matches_ledger` không fail) nhưng nhãn là `denied`/0đ: khoản hoàn không được phép tồn tại. |
| Bước sai đầu tiên | Event **3** `model_override {"delay_days": 5}` — agent ghi đè `delay_days` của DB bằng con số trong ticket ("giao trễ 5 ngày"). |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` đúng hợp đồng (`facts: {}`, không mang secret). Event 2 `investigator` đọc đúng DB: O-202 `delay_days = 2`, chưa đủ ngưỡng SHIP-LATE (≥ 3 ngày) → quote ban đầu là 0. Mọi bước sau (event 4 quote 20.000đ, refund, event 6 reply) đều là hệ quả hợp lệ của con số sai ở event 3. |
| Failure mode | **FM-03 ticket_trust** (`docs/failure_taxonomy.md`). Cùng lỗi xuất hiện trên baseline `swarm C-002@55` (điểm yếu `seed % 5 == 0` của swarm_reference). |
| Đối chứng | `fixed C-002@11`: event 2 giống hệt, **không có** `model_override`; event 3 `quote {"amount_vnd": 0, "reason": "no_entitlement"}`; reply "chưa đủ điều kiện hoàn"; ledger 0 = nhãn. |
| Regression test | `src/tests/test_lab9_trace_case.py`: `test_lab9_trace_case_c002_seeded_regression_is_caught` (assert event 3 là `model_override {"delay_days": 5}`, `DbFactKept` = False, `RefundAmountCorrect` = False, ledger 20.000) và `test_lab9_trace_case_c002_fixed_is_clean` (không override, mọi grader sạch, ledger 0). Đã chạy: fail-detect trên `seeded_regression`, sạch trên `fixed` (`pytest -m student -k lab9` → pass). Item E-002 có `provenance.from_trace = "swarm C-002@55"`. Trong gate, `refund_amount_correct` và `pass_rate` (blocking) chặn nó. |
| Chưa chắc chắn | Trace chỉ ghi *rằng* agent override, không ghi *vì sao* (MockLLM `parse_delay_days` + công tắc `ticket_trust`). Với LLM thật, quyết định tin ticket có thể không để lại event `model_override` (xem biến thể `quiet_override`): khi đó `db_fact_kept` mù, chỉ còn grader kết quả (`refund_amount_correct`, slice `denial`) bắt được. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
