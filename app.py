from flask import Flask, request, jsonify, render_template, Response
import sqlite3
import xml.sax
import xml.etree.ElementTree as ET
from xml.dom import minidom
import os
import datetime
import random
import io

app = Flask(__name__)
DATABASE = "xml_data.db"


# --- Инициализация и работа с БД ---


def get_db():
    """Возвращает соединение с БД."""
    conn = sqlite3.connect(DATABASE)
    # Возвращает строки как объекты, похожие на словари (доступ по имени колонки)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Инициализирует таблицы в БД, если их нет."""
    with app.app_context():  # Работаем в контексте приложения
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute(
                """CREATE TABLE IF NOT EXISTS Files
                         (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS Tags
                         (id INTEGER PRIMARY KEY, name TEXT NOT NULL, file_id INTEGER NOT NULL,
                         FOREIGN KEY (file_id) REFERENCES Files(id) ON DELETE CASCADE)"""  # Добавлено ON DELETE CASCADE
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS Attributes
                         (id INTEGER PRIMARY KEY, name TEXT NOT NULL, value TEXT, tag_id INTEGER NOT NULL,
                         FOREIGN KEY (tag_id) REFERENCES Tags(id) ON DELETE CASCADE)"""  # Добавлено ON DELETE CASCADE
            )
            conn.commit()
        except sqlite3.Error as e:
            print(f"Ошибка инициализации БД: {e}")
        finally:
            if conn:
                conn.close()


# --- XML Handler для SAX парсера ---
class XMLHandler(xml.sax.ContentHandler):
    def __init__(self, file_id, conn):
        self.file_id = file_id
        self.conn = conn
        self.cursor = conn.cursor()  # Создаем курсор один раз
        self.current_tag_id = None
        self._element_stack = []  # Стек для отслеживания вложенности (если понадобится)

    def startElement(self, name, attrs):
        # Добавляем тег
        try:
            self.cursor.execute(
                "INSERT INTO Tags (name, file_id) VALUES (?, ?)", (name, self.file_id)
            )
            self.current_tag_id = self.cursor.lastrowid
            self._element_stack.append(self.current_tag_id)  # Добавляем ID в стек

            # Добавляем атрибуты
            if attrs:  # Проверяем, есть ли атрибуты
                attrs_data = [
                    (attr_name, attr_value, self.current_tag_id)
                    for attr_name, attr_value in attrs.items()
                ]
                if attrs_data:
                    self.cursor.executemany(
                        "INSERT INTO Attributes (name, value, tag_id) VALUES (?, ?, ?)",
                        attrs_data,
                    )
        except sqlite3.Error as e:
            # Лучше пробросить исключение выше, чтобы прервать парсинг
            print(f"Ошибка БД при вставке тега/атрибутов: {e}")
            raise  # Передаем ошибку дальше, чтобы внешний try..except мог ее поймать

    def endElement(self, name):
        # Убираем ID из стека при закрытии тега
        if self._element_stack:
            self._element_stack.pop()

    def characters(self, content):
        clean_content = content.strip()
        if clean_content and self._element_stack:
            # Можно сохранять контент в отдельную таблицу или поле в Tags
            current_tag_id = self._element_stack[-1]
            print(f"Content for tag {current_tag_id}: {clean_content}")
            pass

    pass


# --- Генерация XML ---
def create_sample_xml(file_id="sample"):
    """Создает ElementTree объект с примером XML структуры."""
    root = ET.Element(
        "root", attrib={"generated_at": datetime.datetime.now().isoformat()}
    )

    metadata = ET.SubElement(root, "metadata")
    ET.SubElement(metadata, "source").text = "Flask XML Generator"
    ET.SubElement(metadata, "file_id").text = str(file_id)

    for i in range(1, random.randint(3, 5)):
        category = random.choice(["Electronics", "Books", "Clothing", "Groceries"])
        items_section = ET.SubElement(
            root, "items", attrib={"category": category, "section_id": f"s{i}"}
        )
        for j in range(1, random.randint(4, 8)):
            item_id = f"{category[0].lower()}{i}-{j}"
            item = ET.SubElement(items_section, "item", attrib={"id": item_id})
            ET.SubElement(item, "name").text = f"{category} Item {j}"
            ET.SubElement(item, "price", attrib={"currency": "USD"}).text = str(
                round(random.uniform(5.0, 150.0), 2)
            )
            if random.choice([True, False]):
                ET.SubElement(item, "color").text = random.choice(
                    ["Red", "Blue", "Green", "Black", "White"]
                )
            if random.choice([True, False]):
                item.set("available", random.choice(["true", "false"]))

    for k in range(random.randint(2, 5)):
        ET.SubElement(root, "repeatedTag", attrib={"value": f"rep_{k + 1}"}).text = (
            f"Content {k + 1}"
        )

    return ET.ElementTree(root)


@app.route("/generate-xml")
def generate_xml_file():
    """Генерирует пример XML файла и предлагает его для скачивания."""
    generation_error = None
    try:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"generated_sample_{timestamp}.xml"
        tree = create_sample_xml(file_id=timestamp)

        xml_string = ET.tostring(tree.getroot(), encoding="unicode")
        pretty_xml_string = minidom.parseString(xml_string).toprettyxml(indent="  ")
        xml_bytes = pretty_xml_string.encode("utf-8")

        return Response(
            xml_bytes,
            mimetype="application/xml",
            headers={"Content-Disposition": f"attachment;filename={filename}"},
        )
    except Exception as e:
        generation_error = f"Не удалось сгенерировать XML: {e}"
        print(f"Error generating XML: {e}")  # Логгирование
        # Отображаем ошибку на главной странице
        return render_template("index.html", generation_error=generation_error)


# --- Маршруты для Веб-интерфейса ---


@app.route("/")
def index():
    """Отображает главную страницу интерфейса."""
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def handle_upload():
    """Обрабатывает загрузку XML файла через веб-форму."""
    upload_message = None
    upload_success = False
    file_id_to_rollback = None  # Для отката в случае ошибки парсинга

    if "file" not in request.files:
        upload_message = "Файл не был предоставлен."
        return render_template(
            "index.html", upload_message=upload_message, upload_success=upload_success
        )

    file = request.files["file"]
    filename = file.filename

    if not filename:
        upload_message = "Файл не выбран."
        return render_template(
            "index.html", upload_message=upload_message, upload_success=upload_success
        )

    if not filename.lower().endswith(".xml"):
        upload_message = "Файл должен иметь расширение .xml"
        return render_template(
            "index.html", upload_message=upload_message, upload_success=upload_success
        )

    conn = None
    try:
        conn = get_db()
        conn.execute("PRAGMA foreign_keys = ON")  # Включаем поддержку внешних ключей
        c = conn.cursor()

        # Проверяем, есть ли уже файл с таким именем
        c.execute("SELECT id FROM Files WHERE name = ?", (filename,))
        existing_file = c.fetchone()
        if existing_file:
            upload_message = f"Файл с именем '{filename}' уже существует в базе. Удалите его или загрузите другой."
            return render_template(
                "index.html",
                upload_message=upload_message,
                upload_success=upload_success,
            )

        # Вставляем запись о файле
        c.execute("INSERT INTO Files (name) VALUES (?)", (filename,))
        file_id_to_rollback = c.lastrowid  # Запоминаем ID для возможного отката
        conn.commit()  # Коммитим добавление файла

        # Парсинг XML
        file.seek(0)  # Возвращаемся в начало файла
        parser = xml.sax.make_parser()
        handler = XMLHandler(file_id_to_rollback, conn)  # Передаем ID и соединение
        parser.setContentHandler(handler)
        parser.parse(file)  # Запускаем парсинг

        conn.commit()  # Финальный коммит после успешного парсинга
        upload_message = f"Файл '{filename}' успешно обработан и данные сохранены."
        upload_success = True

    except (xml.sax.SAXParseException, sqlite3.Error) as e:
        # Если ошибка парсинга или ошибка БД во время парсинга
        conn.rollback()  # Откатываем любые изменения в текущей транзакции
        upload_message = (
            f"Ошибка обработки файла '{filename}': {e}. Запись файла была отменена."
        )
        upload_success = False
    except Exception as e:
        # Ловим другие возможные ошибки
        conn.rollback()  # Откатываем
        upload_message = f"Произошла непредвиденная ошибка: {e}"
        upload_success = False
    finally:
        if conn:
            conn.close()  # Закрываем соединение

    return render_template(
        "index.html", upload_message=upload_message, upload_success=upload_success
    )


@app.route("/get-count", methods=["GET"])
def handle_get_count():
    """Обрабатывает запрос количества тегов через веб-форму."""
    filename = request.args.get("filename")
    tag_name = request.args.get("tag_name")
    count_result = None
    count_error = None

    if not filename or not tag_name:
        count_error = "Необходимо указать имя файла и имя тега."
        return render_template(
            "index.html",
            count_error=count_error,
            count_filename=filename,
            count_tag_name=tag_name,
        )

    conn = None
    try:
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT id FROM Files WHERE name = ?", (filename,))
        file_result = c.fetchone()

        if not file_result:
            count_error = f"Файл '{filename}' не найден в базе данных."
        else:
            file_id = file_result["id"]
            c.execute(
                "SELECT COUNT(*) as cnt FROM Tags WHERE file_id = ? AND name = ?",
                (file_id, tag_name),
            )
            count_data = c.fetchone()
            count_result = count_data["cnt"] if count_data else 0
            # Если count_result == 0, не считаем это ошибкой, просто выводим 0
            if count_result == 0:
                count_error = (
                    f"В файле '{filename}' не найдено тегов с именем '{tag_name}'."
                )
                count_result = None  # Убрано, чтобы показывать 0

    except sqlite3.Error as e:
        count_error = f"Ошибка базы данных: {e}"
    except Exception as e:
        count_error = f"Произошла ошибка: {e}"
    finally:
        if conn:
            conn.close()

    return render_template(
        "index.html",
        count_result=count_result,
        count_error=count_error,
        count_filename=filename,
        count_tag_name=tag_name,
    )


@app.route("/get-attributes", methods=["GET"])
def handle_get_attributes():
    """Обрабатывает запрос атрибутов тега через веб-форму."""
    filename = request.args.get("filename")
    tag_name = request.args.get("tag_name")
    attributes_result = None
    attr_error = None

    if not filename or not tag_name:
        attr_error = "Необходимо указать имя файла и имя тега."
        return render_template(
            "index.html",
            attr_error=attr_error,
            attr_filename=filename,
            attr_tag_name=tag_name,
        )

    conn = None
    try:
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT id FROM Files WHERE name = ?", (filename,))
        file_result = c.fetchone()

        if not file_result:
            attr_error = f"Файл '{filename}' не найден в базе данных."
        else:
            file_id = file_result["id"]
            c.execute(
                """
                SELECT DISTINCT attr.name
                FROM Attributes attr
                JOIN Tags t ON attr.tag_id = t.id
                WHERE t.file_id = ? AND t.name = ?
                ORDER BY attr.name
                """,
                (file_id, tag_name),
            )
            attributes_result = [row["name"] for row in c.fetchall()]
            # Пустой список - не ошибка, просто нет атрибутов у этого тега в этом файле.

    except sqlite3.Error as e:
        attr_error = f"Ошибка базы данных: {e}"
    except Exception as e:
        attr_error = f"Произошла ошибка: {e}"
    finally:
        if conn:
            conn.close()

    return render_template(
        "index.html",
        attributes_result=attributes_result,
        attr_error=attr_error,
        attr_filename=filename,
        attr_tag_name=tag_name,
    )


# --- API эндпоинты ---


@app.route("/api/file/read", methods=["POST"])
def api_read_xml_file():
    if "file" not in request.files:
        return jsonify({"result": False, "error": "No file provided"}), 400

    file = request.files["file"]
    filename = file.filename

    if not filename or not filename.lower().endswith(".xml"):
        return jsonify({"result": False, "error": "File must be XML"}), 400

    conn = None
    file_id_to_rollback = None
    try:
        conn = get_db()
        conn.execute("PRAGMA foreign_keys = ON")
        c = conn.cursor()

        c.execute("SELECT id FROM Files WHERE name = ?", (filename,))
        if c.fetchone():
            return (
                jsonify(
                    {"result": False, "error": f"File '{filename}' already exists."}
                ),
                409,
            )

        c.execute("INSERT INTO Files (name) VALUES (?)", (filename,))
        file_id_to_rollback = c.lastrowid
        conn.commit()

        file.seek(0)
        parser = xml.sax.make_parser()
        handler = XMLHandler(file_id_to_rollback, conn)
        parser.setContentHandler(handler)
        parser.parse(file)

        conn.commit()
        return jsonify(
            {
                "result": True,
                "message": f"File '{filename}' processed.",
                "file_id": file_id_to_rollback,
            }
        )

    except (xml.sax.SAXParseException, sqlite3.Error) as e:
        conn.rollback()
        return (
            jsonify(
                {
                    "result": False,
                    "error": f"Error processing file '{filename}': {str(e)}. Rollback performed.",
                }
            ),
            500,
        )
    except Exception as e:
        conn.rollback()
        return (
            jsonify(
                {"result": False, "error": f"An unexpected error occurred: {str(e)}"}
            ),
            500,
        )
    finally:
        if conn:
            conn.close()


@app.route("/api/tags/get-count", methods=["GET"])
def api_get_tag_count():
    filename = request.args.get("filename")
    tag_name = request.args.get("tag_name")

    if not filename or not tag_name:
        return (
            jsonify(
                {"error": "Missing parameters: 'filename' and 'tag_name' are required"}
            ),
            400,
        )

    conn = None
    try:
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT id FROM Files WHERE name = ?", (filename,))
        file_result = c.fetchone()

        if not file_result:
            # Файл не найден - это ошибка 404
            return jsonify({"error": f"File '{filename}' not found"}), 404

        file_id = file_result["id"]
        c.execute(
            "SELECT COUNT(*) as cnt FROM Tags WHERE file_id = ? AND name = ?",
            (file_id, tag_name),
        )
        count = c.fetchone()["cnt"]
        if count == 0:
            # Если тегов нет, возвращаем ошибку согласно требованию
            return (
                jsonify({"error": "В файле отсутствует тег с данным названием"}),
                404,
            )  # Используем 404 Not Found для этого случая
        else:
            # Если теги есть, возвращаем их количество
            return jsonify({"filename": filename, "tag_name": tag_name, "count": count})

    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {e}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/tags/attributes/get", methods=["GET"])
def api_get_tag_attributes():
    filename = request.args.get("filename")
    tag_name = request.args.get("tag_name")

    if not filename or not tag_name:
        return (
            jsonify(
                {"error": "Missing parameters: 'filename' and 'tag_name' are required"}
            ),
            400,
        )

    conn = None
    try:
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT id FROM Files WHERE name = ?", (filename,))
        file_result = c.fetchone()

        if not file_result:
            return jsonify({"error": f"File '{filename}' not found"}), 404

        file_id = file_result["id"]
        c.execute(
            """
            SELECT DISTINCT attr.name
            FROM Attributes attr
            JOIN Tags t ON attr.tag_id = t.id
            WHERE t.file_id = ? AND t.name = ?
            ORDER BY attr.name
            """,
            (file_id, tag_name),
        )
        attributes = [row["name"] for row in c.fetchall()]
        return jsonify(
            {"filename": filename, "tag_name": tag_name, "attributes": attributes}
        )

    except sqlite3.Error as e:
        return jsonify({"error": f"Database error: {e}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500
    finally:
        if conn:
            conn.close()


# --- Запуск приложения ---
if __name__ == "__main__":
    init_db()  # Инициализируем БД при старте
    app.run(debug=True, host="0.0.0.0")
