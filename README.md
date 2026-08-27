# The-Maps

Ứng dụng bản đồ chạy nền trên Windows cho The Isle: Evrima, tự hiển thị vị trí
nhân vật lên bản đồ Gateway.

## Tải xuống

Tải bản `.exe` mới nhất (cả hai phiên bản) tại
[GitHub Releases](https://github.com/EmpireOcean/The-Maps/releases/latest).
Không cần cài Python hoặc tải mã nguồn.

## Hai phiên bản

| | [v1.3](v1.3/) | [v2.0](v2.0/) |
|---|---|---|
| Vị trí | Dán tọa độ từ clipboard, hoặc IslePilot REST | Đọc gói tin UDP, theo thời gian thực, không delay; IslePilot REST làm dự phòng |
| Cài đặt thêm | Không cần | Cần một phần mềm free để đọc gói tin (app tự hỏi và cài giúp nếu bạn đồng ý) |
| Phù hợp với | Muốn gọn nhẹ, không cần cài thêm gì | Muốn trải nghiệm real-time, hướng xoay theo thời gian thực |

Chi tiết cách dùng từng bản nằm trong README riêng của mỗi thư mục:
[v1.3/README.md](v1.3/README.md) và [v2.0/README.md](v2.0/README.md).

## Lưu ý chung

- Bản đồ chỉ mang tính tham khảo; tọa độ có thể sai lệch khi trò chơi cập nhật.
- Windows có thể hiển thị cảnh báo SmartScreen/Defender vì các bản thử nghiệm
  chưa được ký số — đây là false-positive thường gặp với app Python đóng gói
  bằng PyInstaller, không phải app có vấn đề.
- Chỉ tải chương trình từ trang phát hành chính thức của repository này.
