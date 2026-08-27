# The-Maps v2.0

Ứng dụng bản đồ chạy nền trên Windows. So với bản 1.3, v2.0 cập nhật vị trí
và hướng theo thời gian thực, không delay — mượt hơn hẳn so với chỉ dựa vào
clipboard hoặc IslePilot REST.

## Tải xuống

Tải `The-Maps-v2.0.exe` tại [GitHub Releases](https://github.com/EmpireOcean/The-Maps/releases/latest).
Không cần cài Python hoặc tải mã nguồn.

## Cách sử dụng (chế độ mặc định)

1. Tải và mở `The-Maps.exe`.
2. Ứng dụng chạy nền trong khu vực biểu tượng ẩn của thanh tác vụ.
3. Trong trò chơi, bấm vào phần tọa độ để sao chép — hoặc để yên, vị trí sẽ tự
   cập nhật theo thời gian thực, không cần thao tác gì thêm.
4. Bản đồ tự mở và hiển thị vị trí hiện tại cùng hai vị trí gần nhất, với mũi tên
   chỉ hướng di chuyển.
5. Nhấn `Tab` để đóng bản đồ và quay lại trò chơi.
6. Lăn chuột để phóng to/thu nhỏ, kéo chuột (giữ chuột trái) để di chuyển bản đồ
   sau khi đã phóng to, double-click để về lại toàn cảnh.

Click trái biểu tượng The-Maps để mở `Settings`, chuột phải để mở bản đồ hoặc
thoát ứng dụng.

Phiên bản hiện tại chỉ hỗ trợ bản đồ Evrima — Gateway.

## Vị trí theo thời gian thực

- Người dùng cần cài đặt thêm một phần mềm free để đọc gói tin UDP, đưa đến
  trải nghiệm real-time xoay hướng trong game — không cần dán tọa độ thủ công.
- Chỉ đọc dữ liệu của chính bạn — không đọc hay hiển thị vị trí người chơi
  khác.
- Nếu máy chưa cài, The-Maps sẽ hỏi trước khi làm gì cả; bạn đồng ý thì mới
  tự cài giúp (có UAC xác nhận, không cài ngầm).
- Nếu không muốn cài, The-Maps vẫn hoạt động bình thường ở chế độ clipboard +
  IslePilot REST như bản 1.3.

## Kết nối IslePilot (tùy chọn) — chỉ số, nhiệm vụ + vị trí dự phòng

Nếu server bạn chơi có cài IslePilot, mở `Settings` và bấm **"Đăng nhập Steam qua
IslePilot"**. Một cửa sổ đăng nhập Steam thật sẽ hiện ra — mật khẩu của bạn không
đi qua The-Maps, chỉ IslePilot và Steam xử lý. Một lần đăng nhập dùng được cho mọi
server có cài IslePilot, không cần dán link server.

Khi đã kết nối và đang thực sự online trong game, The-Maps sẽ tự hiện thêm:

- **Mini-map** góc trên-trái: bám theo vị trí theo thời gian thực, kèm 4 thanh
  Máu / Stamina / Nước / Food.
- **Bảng nhiệm vụ Prime** góc trên-phải: 10 điều kiện Prime Elder, tích ✓ khi
  hoàn thành.

Hai bảng này chỉ hiện khi cửa sổ game đang active và bạn thực sự online trong
một con dino — tự ẩn khi bạn alt-tab ra ngoài hoặc đang ở menu chính.

Bấm "Ngắt kết nối IslePilot" trong `Settings` bất cứ lúc nào để tắt tính năng
này.

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
