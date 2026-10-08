# MR ALGO Dashboard

หน้ารวมทุกโปรเจกต์ของ Grok Bot: https://thitipathek.github.io/dashboard/

- `index.html` หน้าแดชบอร์ด (HTML/CSS/JS ล้วน ไม่ต้อง build)
- `summary.json` ตัวเลขสรุปที่ `build.py` สร้าง
- `youtube/` และ `trade-tools/` สำเนาหน้าเว็บจาก `/workspace/youtube-gallery/` และ `/workspace/trade-tools/`
- `build.py` อ่านข้อมูลจริงแล้วสร้าง `summary.json` ใหม่ (`python3 build.py --push` เพื่อ commit และ push repo นี้)

ตัวเลขที่อ่านไม่ได้จะแสดงเป็น “—” ไม่มีการประมาณเอง
