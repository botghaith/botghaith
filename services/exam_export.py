"""تصدير نتائج الامتحان PDF و Excel"""
import csv
from pathlib import Path

from services.pdf_service import create_text_pdf


def export_results_csv(exam: dict, results: list, output_path: Path) -> Path:
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["الترتيب", "الاسم", "المعرف", "الصحيحة", "الخاطئة",
                         "المجموع", "النسبة%", "وقت الإنهاء"])
        for i, r in enumerate(results, 1):
            wrong = r["total"] - r["score"]
            name = r.get("full_name") or ""
            username = r.get("username") or ""
            writer.writerow([
                i, name, username, r["score"], wrong, r["total"],
                f"{r['percentage']:.1f}", r.get("completed_at", ""),
            ])
    return output_path


def export_results_pdf(exam: dict, results: list, stats: dict, output_path: Path) -> Path:
    lines = [
        f"نتائج الامتحان: {exam['title']}",
        f"المشاركون: {stats['participants']}",
        f"المتوسط: {stats['avg_score']:.1f}%",
        f"أعلى درجة: {stats['max_score']:.0f}%",
        "",
        "── ترتيب الطلاب ──",
    ]
    for i, r in enumerate(results, 1):
        name = r.get("full_name") or r.get("username") or str(r["user_id"])
        lines.append(
            f"{i}. {name} — {r['score']}/{r['total']} ({r['percentage']:.0f}%)"
        )
    create_text_pdf("\n".join(lines), output_path, title=exam["title"])
    return output_path
