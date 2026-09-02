# The-Maps v2.1

Ứng dụng bản đồ chạy nền trên Windows cho The Isle: Evrima. So với bản 1.3,
v2.1 cập nhật vị trí và hướng theo thời gian thực (qua Npcap), không delay —
kèm HUD chỉ số/nhiệm vụ xuyên chuột và các phím tắt điều khiển nhanh.

## Tải xuống

Tải `The-Maps-v2.1.exe` tại [GitHub Releases](https://github.com/EmpireOcean/The-Maps/releases/latest).
Không cần cài Python hoặc tải mã nguồn.

## Có gì mới ở v2.1

- Phím `M` để hiện/ẩn bản đồ lớn (đổi được trong `Settings`) — không cần
  copy tọa độ mới mở được map như trước nữa.
- Phím `N` để bật/tắt toàn bộ HUD (minimap + bảng nhiệm vụ).
- Click chuột trên HUD giờ xuyên thẳng xuống game như bình thường — chỉ
  thanh kéo mỏng trên đầu mỗi khung mới dùng để đổi vị trí HUD, phần còn
  lại không còn chặn thao tác nữa.
- Con trỏ chuột giả hiện trên HUD khi game đang ẩn con trỏ thật, để bạn vẫn
  thấy chuột đang ở đâu.
- Thêm cách đăng nhập IslePilot bằng token dán tay, không bắt buộc phải qua
  Steam.
- Sửa lỗi khung minimap/bảng nhiệm vụ bị lệch khi chỉ số dài.
- Ứng dụng thoát ổn định hơn — không còn kẹt lại chạy ngầm sau khi bấm Exit.
- Đọc vị trí qua Npcap mượt và nhẹ CPU hơn.

## Vị trí theo thời gian thực

- Người dùng cần cài đặt thêm một phần mềm free (Npcap) để đọc gói tin UDP,
  đưa đến trải nghiệm real-time xoay hướng trong game — không cần dán tọa
  độ thủ công.
- Chỉ đọc dữ liệu của chính bạn — không đọc hay hiển thị vị trí người chơi
  khác.
- Nếu máy chưa cài, The-Maps sẽ hỏi trước khi làm gì cả; bạn đồng ý thì mới
  tự cài giúp (có UAC xác nhận, không cài ngầm).
- Nếu không muốn cài, The-Maps vẫn hoạt động bình thường ở chế độ IslePilot
  REST — xem mục IslePilot bên dưới. Đây là bản dành cho người chấp nhận cài
  thêm Npcap để có trải nghiệm tốt nhất; nếu muốn một bản gọn nhẹ không cần
  cài gì thêm, dùng bản 1.3.

## Kết nối IslePilot (tùy chọn) — chỉ số, nhiệm vụ + HUD

Nếu server bạn chơi có cài IslePilot, mở `Settings` và bấm **"Đăng nhập Steam
qua IslePilot"**. Một cửa sổ đăng nhập Steam thật sẽ hiện ra — mật khẩu của
bạn không đi qua The-Maps, chỉ IslePilot và Steam xử lý. Một lần đăng nhập
dùng được cho mọi server có cài IslePilot, không cần dán link server.

Không đăng nhập Steam được thì bấm **"Nhập token thủ công"** cạnh đó, dán
token overlay lấy từ islepilot.eu.

Khi đã kết nối và đang thực sự online trong game, The-Maps tự hiện thêm 2
khung HUD:

- **Mini-map** góc trên-trái: bám vị trí + hướng nhân vật theo thời gian
  thực, kèm 4 thanh Máu / Stamina / Nước / Food (tự co giãn để số không bị
  đè lên thanh khi chỉ số dài).
  - Lăn chuột trên minimap: zoom in/out.
  - Kéo thanh mỏng trên đầu khung: đổi vị trí HUD trên màn hình.
- **Bảng nhiệm vụ Prime** góc trên-phải: 10 điều kiện Prime Elder, tích ✓
  khi hoàn thành.
  - Bấm vào thanh mỏng trên đầu khung: thu gọn còn 1 dòng tóm tắt / mở lại
    đầy đủ.
  - Tự thu gọn còn 1 dòng khi kết quả đã ngã ngũ (đủ điều kiện Prime Elder,
    hoặc đã trễ mốc growth mà chưa đủ nhiệm vụ).

Nhấn `N` để bật/tắt cả 2 khung HUD cùng lúc, bất cứ lúc nào.

Ngoài thanh kéo mỏng trên đầu mỗi khung, HUD không chặn thao tác chuột —
click ở bất kỳ đâu khác trên HUD (kể cả trên hình bản đồ) đều xuyên thẳng
xuống game như đang không có HUD ở đó. Nếu con trỏ đang ở trong vùng HUD
đúng lúc game ẩn con trỏ thật (lúc đang điều khiển dino), The-Maps tự vẽ một
mũi tên nhỏ tại đúng vị trí chuột để bạn vẫn thấy chuột đang ở đâu — mũi tên
này chỉ để nhìn, không ảnh hưởng gì đến thao tác chuột thật hay việc chơi.

2 khung HUD chỉ hiện khi cửa sổ game đang active và bạn thực sự online trong
một con dino — tự ẩn khi bạn alt-tab ra ngoài hoặc đang ở menu chính.

Bấm "Ngắt kết nối IslePilot" trong `Settings` bất cứ lúc nào để tắt tính năng
này.

## Settings

Click trái biểu tượng The-Maps ở tray để mở. Gồm:

- **Map hiển thị**: chọn map muốn dùng (hiện chỉ có Gateway) — bấm "Lưu" để
  áp dụng.
- **Phím tắt hiện/ẩn bản đồ lớn**: bấm "Đổi phím tắt" rồi nhấn phím muốn
  dùng thay cho `M`. Không chọn được `Tab` hoặc `N` vì 2 phím này đã dùng
  cho việc khác.
- **IslePilot**: đăng nhập Steam / nhập token thủ công / ngắt kết nối — xem
  mục IslePilot ở trên.
- **Local position realtime (Npcap)**: xem trạng thái đọc gói tin, nút
  "Kiểm tra lại" (thử kết nối lại) và "Download & Install Npcap" (tự tải và
  cài nếu máy chưa có).
- Link Subscribe / Discord.

The-Maps tự kiểm tra bản mới trên GitHub Releases mỗi ngày một lần và hỏi
bạn nếu có bản mới hơn — không cần tự vào GitHub kiểm tra.

## Cách sử dụng

1. Tải và mở `The-Maps.exe`. Ứng dụng chạy nền trong khu vực biểu tượng ẩn
   của thanh tác vụ (system tray).
2. Trong game, nhấn `M` để hiện bản đồ lớn — chỉ hoạt động khi cửa sổ game
   đang active (tránh bật nhầm lúc bạn đang gõ chữ ở cửa sổ/app khác).
3. Nhấn `M` hoặc `Tab` để đóng bản đồ, quay lại game.
4. Trên bản đồ lớn:
   - Lăn chuột: phóng to/thu nhỏ quanh vị trí con trỏ.
   - Giữ chuột trái kéo: di chuyển bản đồ sau khi đã phóng to.
   - Double-click: về lại toàn cảnh.
   - 2 nút nhỏ góc dưới-trái (**Migration** màu cam / **Patrol** màu tím):
     bấm để ẩn/hiện từng lớp vùng vẽ trên bản đồ.
5. Click trái biểu tượng The-Maps ở tray để mở `Settings`.

Phiên bản hiện tại chỉ hỗ trợ bản đồ Evrima — Gateway.

## Lưu ý

- Bản đồ chỉ mang tính tham khảo; tọa độ có thể sai lệch khi trò chơi cập nhật.
- Windows có thể hiển thị cảnh báo SmartScreen/Defender vì bản thử nghiệm
  chưa được ký số — đây là false-positive thường gặp với app Python đóng gói
  chưa ký số, không phải app có vấn đề.
- Cài phần mềm phụ trợ nói trên cần quyền Administrator một lần (Windows sẽ
  tự hỏi UAC).
- Kết nối IslePilot cần Microsoft Edge WebView2 Runtime — máy Windows 10/11 hầu
  hết đã có sẵn.
- Chỉ tải chương trình từ trang phát hành chính thức của repository này.

## Build từ source

```
pip install -r requirements.txt
pyinstaller The-Maps.spec
```
