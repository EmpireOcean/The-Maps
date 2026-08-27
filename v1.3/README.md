# The-Maps

Ứng dụng bản đồ chạy nền trên Windows, tự hiển thị vị trí khi nhận được tọa độ
hợp lệ từ clipboard — hoặc theo thời gian thực nếu bạn kết nối với IslePilot.

## Tải xuống

Tải `The-Maps-v1.3.exe` tại [GitHub Releases](https://github.com/EmpireOcean/The-Maps/releases/latest).
Không cần cài Python hoặc tải mã nguồn.

## Cách sử dụng (chế độ mặc định)

1. Tải và mở `The-Maps.exe`.
2. Ứng dụng chạy nền trong khu vực biểu tượng ẩn của thanh tác vụ.
3. Trong trò chơi, bấm vào phần tọa độ để sao chép.
4. Bản đồ tự mở và hiển thị vị trí hiện tại cùng hai vị trí gần nhất, với mũi tên
   chỉ hướng di chuyển.
5. Nhấn `Tab` để đóng bản đồ và quay lại trò chơi.
6. Lăn chuột để phóng to/thu nhỏ, kéo chuột (giữ chuột trái) để di chuyển bản đồ
   sau khi đã phóng to, double-click để về lại toàn cảnh.

Click trái biểu tượng The-Maps để mở `Settings`, chuột phải để mở bản đồ hoặc
thoát ứng dụng.

Phiên bản hiện tại chỉ hỗ trợ bản đồ Evrima — Gateway. Những bản đồ khác sẽ được
xem xét bổ sung trong các bản nâng cấp sau.

## Kết nối IslePilot (tùy chọn) — vị trí, chỉ số, nhiệm vụ theo thời gian thực

Nếu server bạn chơi có cài IslePilot, mở `Settings` và bấm **"Đăng nhập Steam qua
IslePilot"**. Một cửa sổ đăng nhập Steam thật sẽ hiện ra — mật khẩu của bạn không
đi qua The-Maps, chỉ IslePilot và Steam xử lý. Một lần đăng nhập dùng được cho mọi
server có cài IslePilot, không cần dán link server.

Khi đã kết nối và đang thực sự online trong game, The-Maps sẽ tự hiện thêm:

- **Mini-map** góc trên-trái: bám theo vị trí, mũi tên chỉ đúng hướng nhân vật
  đang quay mặt (không phải suy đoán từ di chuyển), kèm 4 thanh Máu / Stamina /
  Nước / Food.
- **Bảng nhiệm vụ Prime** góc trên-phải: 10 điều kiện Prime Elder, tích ✓ khi
  hoàn thành.

Hai bảng này chỉ hiện khi cửa sổ game đang active và bạn thực sự online trong
một con dino — tự ẩn khi bạn alt-tab ra ngoài hoặc đang ở menu chính.

Bấm "Ngắt kết nối IslePilot" trong `Settings` bất cứ lúc nào để tắt tính năng
này và quay lại chế độ mặc định.

## Lưu ý

- Bản đồ chỉ mang tính tham khảo; tọa độ có thể sai lệch khi trò chơi cập nhật.
- Windows có thể hiển thị SmartScreen vì bản thử nghiệm chưa được ký số.
- Kết nối IslePilot cần Microsoft Edge WebView2 Runtime — máy Windows 10/11 hầu
  hết đã có sẵn.
- Chỉ tải chương trình từ trang phát hành chính thức của repository này.
