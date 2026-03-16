"""
Email camouflage locales.

Each locale provides pools of realistic subject lines, filenames,
and body text for a specific language and region. The generated
content should be indistinguishable from normal correspondence
in that locale.

To add a new locale, define a class with generate_subject(),
generate_filename(), and generate_body() methods, then register
it in the LOCALES dict at the bottom of this file.
"""

import random
import time


class _Base:
    """Shared helpers."""

    @staticmethod
    def _rand_num():
        styles = [
            lambda: str(random.randint(100, 9999)),
            lambda: f"{random.randint(1, 999)}-{random.randint(1, 99)}",
        ]
        return random.choice(styles)()

    @staticmethod
    def _ts():
        return time.strftime("%Y%m%d")


class RussianLocale(_Base):
    """
    Russian business and personal email patterns.
    Primary target: Yandex Mail, Mail.ru.
    """

    @staticmethod
    def _rand_date():
        styles = [
            lambda: f"{random.randint(1,28):02d}.{random.randint(1,12):02d}",
            lambda: f"{random.randint(1,28):02d}.{random.randint(1,12):02d}.{random.choice(['2025','2026'])}",
            lambda: random.choice([
                "январь", "февраль", "март", "апрель", "май", "июнь",
                "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
            ]),
        ]
        return random.choice(styles)()

    @staticmethod
    def _rand_num_ru():
        styles = [
            lambda: str(random.randint(100, 9999)),
            lambda: f"{random.randint(1,999)}-{random.randint(1,99)}",
            lambda: f"{random.choice(['А','Б','В','К','М','Н','П','С'])}-{random.randint(100,9999)}",
        ]
        return random.choice(styles)()

    @classmethod
    def generate_subject(cls) -> str:
        n = cls._rand_num_ru()
        d = cls._rand_date()

        pool = [
            f"Отчёт за {d}",
            "Документы на подпись",
            f"Счёт-фактура №{n}",
            f"Акт выполненных работ №{n}",
            f"Накладная №{n}",
            f"Договор №{n}",
            "Смета на утверждение",
            f"Протокол совещания {d}",
            f"Бухгалтерские документы за {d}",
            "Выписка из реестра",
            "Справка по запросу",
            "Заявление на отпуск",
            f"Табель учёта за {d}",
            f"Коммерческое предложение №{n}",
            "Презентация проекта",
            "Re: Согласование бюджета",
            f"Re: Вопрос по договору №{n}",
            "Fwd: Письмо от контрагента",
            f"Подтверждение заказа №{n}",
            "Квитанция об оплате",
            f"Чек №{n} от {d}",
            f"Возврат товара - заявка №{n}",
            f"Доставка заказа №{n}",
            "Фотографии",
            "Фото с поездки",
            "Сканы документов",
            "Как договаривались",
            "Пересылаю файлы",
            "По вашей просьбе",
            f"Материалы к встрече {d}",
            "Рецепт",
            "Адреса и контакты",
            "Файлы для печати",
            "Re: Спасибо!",
            "Re: Встреча",
            "Re: Вопрос",
            "Напоминание",
            "Обновлённая версия",
            "Исправленный документ",
            f"Резервная копия {d}",
        ]
        return random.choice(pool)

    @classmethod
    def generate_filename(cls) -> str:
        n = cls._rand_num_ru()
        ts = cls._ts()
        d = cls._rand_date()

        pool = [
            f"scan_{random.randint(1,999):03d}.pdf",
            f"IMG_{ts}_{random.randint(1000,9999)}.jpg",
            f"photo_{random.randint(1,50)}.jpg",
            f"скан_{random.randint(1,99)}.pdf",
            "отчёт.xlsx",
            f"отчёт_{d}.xlsx",
            f"договор_{n}.docx",
            f"акт_{n}.pdf",
            f"накладная_{n}.pdf",
            f"счёт_{n}.pdf",
            "смета.xlsx",
            "протокол.docx",
            "справка.pdf",
            "выписка.pdf",
            "заявление.docx",
            "презентация.pdf",
            "архив.zip",
            "документы.zip",
            f"backup_{ts}.zip",
            "файлы.rar",
            "данные.zip",
            "1С_выгрузка.zip",
            "data.bin",
            f"export_{ts}.dat",
            "файл.dat",
        ]
        return random.choice(pool)

    @staticmethod
    def generate_body(has_attachment: bool) -> str:
        if not has_attachment:
            return None

        pool = [
            "Добрый день!\nФайлы во вложении.\nС уважением.",
            "Здравствуйте,\nПересылаю документы как договаривались.",
            "Привет!\nВот файлы, посмотри.",
            "Высылаю запрошенные документы.\nЕсли будут вопросы - пишите.",
            "Добрый день,\nВо вложении обновлённая версия.\nС уважением.",
            "Пересылаю.\nДай знать если всё ок.",
            "Здравствуйте!\nПрикрепляю файлы по нашему разговору.",
            "Высылаю данные. Жду обратной связи.",
            "Файлы готовы, отправляю.",
            "Как и обещал - файлы в приложении.",
            "Добрый день!\nНаправляю материалы.\nС уважением.",
            "Высылаю сканы, оригиналы отправлю почтой.",
        ]
        return random.choice(pool)


class EnglishLocale(_Base):
    """
    English business and personal email patterns.
    For use with Gmail, Outlook, Yahoo, or any English-language provider.
    """

    @classmethod
    def generate_subject(cls) -> str:
        n = cls._rand_num()
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        d = random.choice(months)

        pool = [
            f"Report for {d}",
            "Documents for review",
            f"Invoice #{n}",
            f"Receipt #{n}",
            f"Contract #{n}",
            f"Meeting notes - {d}",
            "Quarterly summary",
            "Budget update",
            "Re: Project files",
            "Re: Follow up",
            "Fwd: From accounting",
            f"Order confirmation #{n}",
            f"Delivery notice #{n}",
            f"Expense report - {d}",
            "Photos",
            "Scanned documents",
            "As discussed",
            "Files attached",
            "Per your request",
            "Materials for the meeting",
            "Updated version",
            "Revised document",
            f"Backup - {d}",
            "Re: Thanks",
            "Re: Question",
            "Reminder",
            "FYI",
            "Quick update",
            "Re: Schedule",
            f"Tax documents #{n}",
        ]
        return random.choice(pool)

    @classmethod
    def generate_filename(cls) -> str:
        n = cls._rand_num()
        ts = cls._ts()

        pool = [
            f"scan_{random.randint(1,999):03d}.pdf",
            f"IMG_{ts}_{random.randint(1000,9999)}.jpg",
            f"photo_{random.randint(1,50)}.jpg",
            "report.xlsx",
            f"report_{ts}.xlsx",
            f"contract_{n}.docx",
            f"invoice_{n}.pdf",
            f"receipt_{n}.pdf",
            "estimate.xlsx",
            "minutes.docx",
            "certificate.pdf",
            "statement.pdf",
            "application.docx",
            "presentation.pdf",
            "archive.zip",
            "documents.zip",
            f"backup_{ts}.zip",
            "files.zip",
            "data.bin",
            f"export_{ts}.dat",
        ]
        return random.choice(pool)

    @staticmethod
    def generate_body(has_attachment: bool) -> str:
        if not has_attachment:
            return None

        pool = [
            "Hi,\nPlease find the files attached.\nBest regards.",
            "Hello,\nForwarding the documents as discussed.",
            "Hey,\nHere are the files, take a look.",
            "Sending the requested documents.\nLet me know if you have questions.",
            "Hi,\nAttached is the updated version.\nBest regards.",
            "Forwarding. Let me know if it looks good.",
            "Hello,\nAttaching the files from our conversation.",
            "Sending the data. Looking forward to your feedback.",
            "Files are ready, sending now.",
            "As promised, files attached.",
            "Hi,\nSending the materials over.\nBest regards.",
            "Scans attached. Originals in the mail.",
        ]
        return random.choice(pool)


class NeutralLocale(_Base):
    """
    Language-neutral patterns. ASCII-only subjects and filenames.
    Minimal text. For use when no specific locale is appropriate.
    """

    @classmethod
    def generate_subject(cls) -> str:
        n = cls._rand_num()
        ts = cls._ts()

        pool = [
            f"Re: #{n}",
            f"Fwd: #{n}",
            "Files",
            "Documents",
            "Update",
            "Re: Update",
            "Data",
            f"Backup {ts}",
            "Report",
            "Info",
        ]
        return random.choice(pool)

    @classmethod
    def generate_filename(cls) -> str:
        n = cls._rand_num()
        ts = cls._ts()

        pool = [
            f"scan_{random.randint(1,999):03d}.pdf",
            f"IMG_{ts}_{random.randint(1000,9999)}.jpg",
            f"doc_{n}.pdf",
            f"data_{ts}.zip",
            f"backup_{ts}.zip",
            f"file_{n}.dat",
            f"export_{ts}.bin",
            "report.pdf",
            "archive.zip",
        ]
        return random.choice(pool)

    @staticmethod
    def generate_body(has_attachment: bool) -> str:
        if not has_attachment:
            return None

        pool = [
            "See attached.",
            "Files attached.",
            "Forwarding.",
            "As discussed.",
            "Please review.",
        ]
        return random.choice(pool)


# ── Registry ──

LOCALES = {
    "ru": RussianLocale,
    "en": EnglishLocale,
    "neutral": NeutralLocale,
}

DEFAULT_LOCALE = "ru"


def get_locale(name: str):
    locale = LOCALES.get(name)
    if locale is None:
        raise ValueError(
            f"Unknown locale '{name}'. Available: {', '.join(LOCALES.keys())}"
        )
    return locale
