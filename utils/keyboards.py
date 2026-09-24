from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

# ── القائمة الرئيسية ──

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["📚 الترجمة", "📄 أدوات PDF"],
        ["📋 واجهة تقرير", "📝 الامتحانات"],
        ["🧑‍🎓 حسابي", "ℹ️ المساعدة"],
        ["🛡️ حماية الكروبات"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📊 لوحة التحكم", "📈 الإحصائيات"],
        ["👥 المستخدمون", "🏆 ترتيب الطلاب"],
        ["📝 إنشاء امتحان", "📋 إدارة الامتحانات"],
        ["❓ الأسئلة الجاهزة", "➕ سؤال جديد"],
        ["📢 إشعار جماعي", "📺 إدارة القنوات"],
        ["📜 سجل النشاط", "🛠️ حالة الخدمات"],
        ["🛡️ قنوات الحماية"],
        ["🏠 القائمة الرئيسية"],
    ],
    resize_keyboard=True,
)

# ── الترجمة ──

def translation_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["📝 ترجمة نص", "📁 ترجمة ملف"],
            ["🖼️ ترجمة صورة", "🔙 القائمة الرئيسية"],
            ["🔠 صغير", "🔠 متوسط", "🔠 كبير"],
        ],
        resize_keyboard=True,
    )

def translation_direction_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("عربي → إنجليزي", callback_data="tr_dir_ar_en"),
            InlineKeyboardButton("إنجليزي → عربي", callback_data="tr_dir_en_ar"),
        ],
        [InlineKeyboardButton("🔄 اكتشاف تلقائي", callback_data="tr_dir_auto")],
    ])


def translation_direction_reply_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["عربي → إنجليزي", "إنجليزي → عربي"],
            ["🔄 اكتشاف تلقائي", "🔙 القائمة الرئيسية"],
        ],
        resize_keyboard=True,
    )


def translation_color_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⬛ أسود", callback_data="tr_color_black"),
            InlineKeyboardButton("🔴 أحمر", callback_data="tr_color_red"),
        ],
        [
            InlineKeyboardButton("🔵 أزرق", callback_data="tr_color_blue"),
            InlineKeyboardButton("🟢 أخضر", callback_data="tr_color_green"),
        ],
    ])


_DIRECTION_FROM_TEXT = {
    "عربي → إنجليزي": "ar_en",
    "إنجليزي → عربي": "en_ar",
    "🔄 اكتشاف تلقائي": "auto",
}


def parse_direction_text(text: str) -> str | None:
    return _DIRECTION_FROM_TEXT.get((text or "").strip())

# ── PDF Tools ──

def pdf_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🖼️ صور → PDF", "📷 PDF → صور"],
            ["📄 Word → PDF", "📝 PDF → Word"],
            ["🔗 دمج PDF", "✂️ تقسيم PDF"],
            ["🗜️ ضغط PDF", "📖 استخراج نص"],
            ["🔄 إعادة ترتيب", "📑 دمج سلايدات"],
            ["🔙 القائمة الرئيسية"],
        ],
        resize_keyboard=True,
    )


# ── Exams ──

def exam_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["➕ إنشاء امتحان", "▶️ حل امتحان"],
            ["📊 نتائج امتحاناتي", "🏆 ترتيب الطلاب"],
            ["📋 نتائجي", "🔙 القائمة الرئيسية"],
        ],
        resize_keyboard=True,
    )

def exam_skip_keyboard(callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ تخطي", callback_data=callback)],
    ])

def exam_builder_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة سؤال", callback_data="eb_add_q")],
        [
            InlineKeyboardButton("👁️ معاينة", callback_data="eb_preview"),
            InlineKeyboardButton("📢 نشر الامتحان", callback_data="eb_publish"),
        ],
        [InlineKeyboardButton("🔀 تبديل: متعدد الإجابات", callback_data="eb_toggle_multi")],
    ])

def exam_question_manage_keyboard(index: int, total: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("✏️ تعديل", callback_data=f"eq_edit_{index}"),
            InlineKeyboardButton("🗑️ حذف", callback_data=f"eq_del_{index}"),
            InlineKeyboardButton("📋 نسخ", callback_data=f"eq_copy_{index}"),
        ],
        [
            InlineKeyboardButton("🖼️ صورة", callback_data=f"eq_img_{index}"),
            InlineKeyboardButton("📝 ملاحظة", callback_data=f"eq_note_{index}"),
        ],
    ]
    move = []
    if index > 0:
        move.append(InlineKeyboardButton("⬆️", callback_data=f"eq_up_{index}"))
    if index < total - 1:
        move.append(InlineKeyboardButton("⬇️", callback_data=f"eq_down_{index}"))
    if move:
        rows.append(move)
    return InlineKeyboardMarkup(rows)

def exam_correct_pick_keyboard(options: list, selected: int = -1) -> InlineKeyboardMarkup:
    buttons = []
    for i, opt in enumerate(options):
        mark = "☑" if i == selected else "☐"
        label = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح", "ط", "ي"][i] if i < 10 else str(i+1)
        buttons.append([InlineKeyboardButton(
            f"{mark} {label}) {opt[:28]}", callback_data=f"eq_pick_{i}"
        )])
    buttons.append([InlineKeyboardButton("💾 حفظ السؤال", callback_data="eq_save_q")])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="eq_cancel_q")])
    return InlineKeyboardMarkup(buttons)

def exam_start_keyboard(exam_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ بدء الامتحان", callback_data=f"exam_start_{exam_id}")],
    ])

def exam_creator_results_keyboard(exam_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 عرض النتائج", callback_data=f"er:view:{exam_id}")],
        [
            InlineKeyboardButton("📄 PDF", callback_data=f"er:pdf:{exam_id}"),
            InlineKeyboardButton("📊 Excel", callback_data=f"er:xls:{exam_id}"),
        ],
    ])

# ── Channel Subscription ──

def channel_subscribe_keyboard(channels: list) -> InlineKeyboardMarkup:
    buttons = []
    for ch in channels:
        label = ch.get("title") or f"@{ch.get('username', '')}"
        link = ch.get("link", "")
        if link:
            buttons.append([InlineKeyboardButton(f"📺 {label}", url=link)])
    buttons.append([InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_subscription")])
    return InlineKeyboardMarkup(buttons)


def admin_channels_menu_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    toggle_label = "⛔ تعطيل الاشتراك الإجباري" if enabled else "✅ تفعيل الاشتراك الإجباري"
    toggle_data = "adm_ch_req_off" if enabled else "adm_ch_req_on"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة قناة", callback_data="adm_ch_add")],
        [InlineKeyboardButton(toggle_label, callback_data=toggle_data)],
        [InlineKeyboardButton("🔄 تحديث القائمة", callback_data="adm_ch_list")],
    ])


def admin_channels_list_keyboard(channels: list) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        status = "🟢" if ch.get("is_active", 1) else "🔴"
        label = ch.get("title") or f"@{ch['username']}"
        rows.append([
            InlineKeyboardButton(
                f"{status} {label[:30]}",
                callback_data=f"adm_ch_view_{ch['id']}",
            )
        ])
    if not rows:
        rows.append([InlineKeyboardButton("لا توجد قنوات", callback_data="adm_noop")])
    rows.append([InlineKeyboardButton("➕ إضافة قناة", callback_data="adm_ch_add")])
    return InlineKeyboardMarkup(rows)


def admin_channel_actions_keyboard(channel_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle = "⛔ تعطيل" if is_active else "✅ تفعيل"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ اليوزر", callback_data=f"adm_ch_edit_user_{channel_id}"),
            InlineKeyboardButton("🔗 الرابط", callback_data=f"adm_ch_edit_link_{channel_id}"),
        ],
        [InlineKeyboardButton("📝 الاسم", callback_data=f"adm_ch_edit_title_{channel_id}")],
        [
            InlineKeyboardButton(toggle, callback_data=f"adm_ch_toggle_{channel_id}"),
            InlineKeyboardButton("🗑️ حذف", callback_data=f"adm_ch_del_{channel_id}"),
        ],
        [InlineKeyboardButton("🔙 قائمة القنوات", callback_data="adm_ch_list")],
    ])

# ── Exam Answer Keyboard ──

def exam_answer_keyboard(question_index: int, options: list) -> InlineKeyboardMarkup:
    labels = ["أ", "ب", "ج", "د"]
    buttons = []
    for i, opt in enumerate(options):
        label = labels[i] if i < len(labels) else str(i + 1)
        text = f"{label}) {opt[:40]}"
        buttons.append([InlineKeyboardButton(
            text, callback_data=f"exam_ans_{question_index}_{i}"
        )])
    return InlineKeyboardMarkup(buttons)

# ── Admin Exam Creation ──

def admin_exam_source_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("من أسئلة جاهزة", callback_data="admin_exam_ready")],
    ])


def admin_exams_list_keyboard(exams: list) -> InlineKeyboardMarkup:
    rows = []
    for e in exams[:12]:
        status = "🟢" if e.get("is_active", 1) and e.get("is_published", 1) else "🔴"
        label = f"{status} {e['title'][:28]}"
        rows.append([InlineKeyboardButton(label, callback_data=f"adm_exam_{e['exam_id']}")])
    if not rows:
        rows.append([InlineKeyboardButton("لا توجد امتحانات", callback_data="adm_noop")])
    return InlineKeyboardMarkup(rows)


def admin_exam_actions_keyboard(exam_id: str, is_active: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📊 النتائج", callback_data=f"adm_exam_res_{exam_id}")],
    ]
    if is_active:
        rows.append([InlineKeyboardButton("⛔ إيقاف الامتحان", callback_data=f"adm_exam_off_{exam_id}")])
    rows.append([InlineKeyboardButton("🔙 قائمة الامتحانات", callback_data="adm_exams_list")])
    return InlineKeyboardMarkup(rows)


def _user_button_label(seq: int, user: dict) -> str:
    name = (user.get("full_name") or "").strip() or "بدون اسم"
    uname = (user.get("username") or "").strip().lstrip("@")
    label = f"{seq}. {name}"
    if uname:
        label += f" @{uname}"
    return label[:64]


def admin_users_page_keyboard(
    users: list, page: int, page_size: int = 8,
) -> InlineKeyboardMarkup:
    total = len(users)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = max(0, min(page, pages - 1))
    start = page * page_size
    rows = []
    for seq, user in enumerate(users[start:start + page_size], start + 1):
        rows.append([
            InlineKeyboardButton(
                _user_button_label(seq, user),
                callback_data=f"adm_um_{user['user_id']}_{page}",
            )
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ السابق", callback_data=f"adm_up_{page - 1}"))
    if start + page_size < total:
        nav.append(InlineKeyboardButton("التالي ▶️", callback_data=f"adm_up_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔍 بحث", callback_data="adm_users_search")])
    return InlineKeyboardMarkup(rows)


def admin_user_chat_keyboard(
    user_id: int,
    msg_page: int,
    msg_pages: int,
    list_page: int,
    prev_user: tuple[int, int] | None,
    next_user: tuple[int, int] | None,
) -> InlineKeyboardMarkup:
    rows = []
    people = []
    if prev_user:
        people.append(InlineKeyboardButton(
            "👤 السابق", callback_data=f"adm_um_{prev_user[0]}_{prev_user[1]}",
        ))
    if next_user:
        people.append(InlineKeyboardButton(
            "التالي 👤", callback_data=f"adm_um_{next_user[0]}_{next_user[1]}",
        ))
    if people:
        rows.append(people)
    pages = []
    if msg_page < msg_pages - 1:
        pages.append(InlineKeyboardButton(
            "◀️ أقدم", callback_data=f"adm_uc_{user_id}_{msg_page + 1}_{list_page}",
        ))
    if msg_page > 0:
        pages.append(InlineKeyboardButton(
            "أحدث ▶️", callback_data=f"adm_uc_{user_id}_{msg_page - 1}_{list_page}",
        ))
    if pages:
        rows.append(pages)
    rows.append([
        InlineKeyboardButton("🔙 المستخدمون", callback_data=f"adm_up_{list_page}"),
        InlineKeyboardButton("🔍 بحث", callback_data="adm_users_search"),
    ])
    return InlineKeyboardMarkup(rows)


def admin_user_search_keyboard(users: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for user in users[:10]:
        name = (user.get("full_name") or "").strip() or "بدون اسم"
        rows.append([
            InlineKeyboardButton(
                f"👤 {name}"[:64],
                callback_data=f"adm_um_{user['user_id']}_0",
            )
        ])
    rows.append([
        InlineKeyboardButton("👥 القائمة", callback_data="adm_up_0"),
        InlineKeyboardButton("🔍 بحث", callback_data="adm_users_search"),
    ])
    return InlineKeyboardMarkup(rows)


def admin_users_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 قائمة المستخدمين", callback_data="adm_up_0"),
            InlineKeyboardButton("🔍 بحث", callback_data="adm_users_search"),
        ],
    ])


def guard_input_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["✅ تم"], ["🔙 القائمة الرئيسية"]],
        resize_keyboard=True,
    )


def guard_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة قنوات مسموحة", callback_data="gg_add")],
        [InlineKeyboardButton("📋 القنوات المسموحة", callback_data="gg_list")],
    ])


def guard_groups_keyboard(groups: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for group in groups[:20]:
        title = (group.get("title") or "كروب").strip() or "كروب"
        rows.append([
            InlineKeyboardButton(title[:40], callback_data=f"gg_pick_{group['chat_id']}")
        ])
    return InlineKeyboardMarkup(rows)


def guard_channel_delete_keyboard(
    rows_data: list[tuple[str, str]], *, with_add: bool = True,
) -> InlineKeyboardMarkup:
    rows = []
    for label, data in rows_data:
        rows.append([InlineKeyboardButton(label[:64], callback_data=data)])
    if with_add:
        rows.append([InlineKeyboardButton("➕ إضافة قنوات", callback_data="gg_add")])
    return InlineKeyboardMarkup(rows)


def admin_dashboard_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 إحصائيات", callback_data="adm_stats"),
            InlineKeyboardButton("📜 نشاط", callback_data="adm_activity"),
        ],
        [
            InlineKeyboardButton("📋 امتحانات", callback_data="adm_exams_list"),
            InlineKeyboardButton("🛠️ خدمات", callback_data="adm_services"),
        ],
    ])

def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["❌ إلغاء"]], resize_keyboard=True)
