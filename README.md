<<<<<<< HEAD
# بوت تيليجرام تعليمي متكامل لطلاب الجامعات
# من إعداد المهندس غيث اسعد

## المميزات

| القسم | الوصف |
|-------|-------|
| 📚 الترجمة | ترجمة نصوص وملفات (عربي ↔ إنجليزي) محلياً |
| 🧠 MCQ | توليد أسئلة اختيار من متعدد من الملفات |
| 📄 PDF | تحويل، دمج، تقسيم، ضغط، استخراج نص |
| 📑 تلخيص | تلخيص المحاضرات وإخراج PDF |
| 📝 امتحانات | اختبارات بروابط خاصة وتصحيح تلقائي |
| 🤖 AI | مساعد دراسي (تقارير، شرح، واجبات) |
| 🧑‍🎓 حسابات | نقاط، نتائج، ترتيب الطلاب |
| 🔧 أدمن | إدارة كاملة + اشتراك قناة إجباري |

**كل شيء يعمل محلياً — بدون API مدفوع**

---

## التثبيت

### Windows / Linux / Mac

```bash
cd bot1
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

pip install -r requirements.txt
python setup_translate.py    # تحميل حزم الترجمة (أول مرة)
python bot.py
```

### Termux (أندرويد)

```bash
pkg install python python-pip
pip install -r requirements.txt
python setup_translate.py
python bot.py
```

---

## الإعداد

انسخ `.env.example` إلى `.env` وعدّل:

```env
BOT_TOKEN=your_token_here
ADMIN_USERNAME=qhaith
```

| المتغير | الوصف |
|---------|-------|
| `BOT_TOKEN` | توكن البوت من [@BotFather](https://t.me/BotFather) |
| `ADMIN_USERNAME` | يوزر الأدمن بدون @ |

---

## الأوامر

| الأمر | الوظيفة |
|-------|---------|
| `/start` | بدء البوت ورسالة الترحيب |
| `/help` | دليل الاستخدام |
| `/admin` | لوحة تحكم الأدمن |
| `/cancel` | إلغاء العملية الحالية |
| `/exam <رمز>` | بدء امتحان برمزه |

---

## لوحة الأدمن (`/admin`)

- **📊 الإحصائيات** — إحصائيات شاملة
- **👥 المستخدمون** — قائمة الطلاب
- **📝 إنشاء امتحان** — من نص/ملف/أسئلة جاهزة + رابط
- **❓ إدارة الأسئلة** — إضافة أسئلة جاهزة
- **📢 إشعار جماعي** — رسالة لجميع المستخدمين
- **📺 إعداد القناة** — رابط القناة + اشتراك إجباري

### تفعيل الاشتراك الإجباري في القناة

1. أضف البوت **مشرفاً** في قناتك
2. `/admin` → **📺 إعداد القناة**
3. أرسل يوزر القناة: `@yourchannel`
4. أرسل رابط القناة: `https://t.me/yourchannel`

---

## هيكل المشروع

```
bot1/
├── bot.py              # نقطة التشغيل
├── config.py           # الإعدادات
├── setup_translate.py  # تثبيت الترجمة
├── database/db.py      # SQLite
├── handlers/           # معالجات الأقسام
├── services/           # الخدمات المحلية
└── utils/              # أدوات مساعدة
```

---

## ملاحظات

- الترجمة تستخدم **Argos Translate** (محلي 100%)
- التلخيص يستخدم **Sumy + NLTK** (محلي)
- الذكاء الاصطناعي يعمل بمعالجة نصوص محلية (بدون ChatGPT)
- البيانات في `data/bot.db` (SQLite)

---

**من إعداد المهندس غيث اسعد**
=======
## Hi there 👋

<!--
**botghaith/botghaith** is a ✨ _special_ ✨ repository because its `README.md` (this file) appears on your GitHub profile.

Here are some ideas to get you started:

- 🔭 I’m currently working on ...
- 🌱 I’m currently learning ...
- 👯 I’m looking to collaborate on ...
- 🤔 I’m looking for help with ...
- 💬 Ask me about ...
- 📫 How to reach me: ...
- 😄 Pronouns: ...
- ⚡ Fun fact: ...
-->
>>>>>>> 677b80e2cc5d016ca2285fba60034ef4f7844959
