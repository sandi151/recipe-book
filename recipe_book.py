"""Мой рецептник. Часть 1 (дедлайн 16 ноября).

здесь есть:
  * класс Database      - база данных SQLite (5 таблиц), добавление и чтение рецептов;
  * класс RecipeForm    - форма добавления рецепта (окно из recipe_form.ui);
  * класс MainWindow    - главное окно (окно из main_window.ui): таблица, поиск, фильтры,
                          меню, горячие клавиши;
  * функция main()      - запуск приложения.

здесь пока нет (слово TODO): редактирование и удаление рецептов,
карточка рецепта, таймер, список покупок, окно «Что приготовить?», экспорт.

Запуск: положит рядом файлы main_window.ui и recipe_form.ui, затем
    pip install PyQt6
    python recipe_book_part1.py
"""
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

from PyQt6 import uic
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog,
                             QHeaderView, QMainWindow, QMessageBox, QTableWidgetItem)

# ======================================================================

BASE_DIR = Path(__file__).resolve().parent      # папка, где лежит этот файл
DB_PATH = BASE_DIR / "recipes.db"               # файл базы данных
PHOTOS_DIR = BASE_DIR / "photos"                # сюда копируются фото блюд

CATEGORIES = ["Завтраки", "Супы", "Основные блюда", "Салаты", "Выпечка", "Десерты", "Напитки"]
UNITS = ["г", "кг", "мл", "л", "шт", "ч. л.", "ст. л.", "по вкусу"]
IMAGE_FILTER = "Изображения (*.png *.jpg *.jpeg)"
COLUMNS = ["Название", "Категория", "Время, мин", "Порции"]

# Оформление приложения (CSS для Qt, раздел F26 в ТЗ)
STYLE = """
QPushButton { padding: 6px 14px; border: 1px solid #b8b8b8; border-radius: 6px; background: #f5f5f5; }
QPushButton:hover { background: #e8f1e4; border-color: #7aa874; }
QPushButton:disabled { color: #a0a0a0; }
QPushButton:default { background: #4f9a4a; color: white; }
QHeaderView::section { background: #eef3ec; padding: 5px; border: none; border-bottom: 1px solid #c9d6c6; }
QTableWidget { selection-background-color: #cfe5cb; selection-color: black; }
"""


# Вспомогательные функции

def normalize(name: str) -> str:
    """Название продукта в едином виде: без лишних пробелов, строчными буквами."""
    return " ".join(name.split()).lower()


def _py_lower(value):
    """SQLite понижает регистр только у латиницы, поэтому для русского делаем это в Python."""
    return value.lower() if isinstance(value, str) else value



# Работа с базой данных

class Database:
    """Все запросы к базе собраны здесь. Ни от кого не наследуется."""

    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row          # строки можно читать по имени: row["title"]
        self.conn.execute("PRAGMA foreign_keys = ON")  # включаем проверку связей между таблицами
        self.conn.create_function("PYLOWER", 1, _py_lower)
        self._create_tables()
        self._seed_categories()

    def close(self) -> None:
        self.conn.close()

    # ---------- создание таблиц ----------

    def _create_tables(self) -> None:
        with self.conn:  # with = «сохранить изменения, а при ошибке откатить»
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS categories (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS recipes (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT NOT NULL,
                    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                    cook_time   INTEGER NOT NULL DEFAULT 0,
                    servings    INTEGER NOT NULL DEFAULT 1,
                    steps       TEXT NOT NULL DEFAULT '',
                    photo_path  TEXT
                );
                CREATE TABLE IF NOT EXISTS ingredients (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS recipe_ingredients (
                    recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
                    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
                    amount        REAL NOT NULL DEFAULT 0,
                    unit          TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (recipe_id, ingredient_id)
                );
                CREATE TABLE IF NOT EXISTS shopping_list (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
                    amount        TEXT NOT NULL DEFAULT '',
                    bought        INTEGER NOT NULL DEFAULT 0
                );
            """)

    def _seed_categories(self) -> None:
        """Заполняет категории при самом первом запуске (раздел 5.3 ТЗ)."""
        count = self.conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if count == 0:
            with self.conn:
                self.conn.executemany("INSERT INTO categories (name) VALUES (?)",
                                      [(name,) for name in CATEGORIES])

    # ---------- чтение справочников ----------

    def categories(self) -> list[dict]:
        rows = self.conn.execute("SELECT id, name FROM categories ORDER BY id")
        return [dict(row) for row in rows]

    def _ingredient_id(self, name: str) -> int:
        """id продукта по названию. Если такого продукта ещё нет, создаёт его."""
        name = normalize(name)
        self.conn.execute("INSERT OR IGNORE INTO ingredients (name) VALUES (?)", (name,))
        row = self.conn.execute("SELECT id FROM ingredients WHERE name = ?", (name,)).fetchone()
        return row["id"]

    def _ingredients_of(self, recipe_id: int) -> list[dict]:
        """Ингредиенты одного рецепта (запрос сразу к двум таблицам через JOIN)."""
        rows = self.conn.execute("""
            SELECT i.name, ri.amount, ri.unit
            FROM recipe_ingredients ri
            JOIN ingredients i ON i.id = ri.ingredient_id
            WHERE ri.recipe_id = ?
            ORDER BY ri.rowid
        """, (recipe_id,))
        return [dict(row) for row in rows]

    # ---------- рецепты ----------

    def add_recipe(self, title, category_id, cook_time, servings, steps, photo_path,
                   ingredients) -> int:
        """Добавляет рецепт. ingredients - список кортежей (название, количество, единица).
        Возвращает id нового рецепта."""
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO recipes (title, category_id, cook_time, servings, steps, photo_path) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (title, category_id, cook_time, servings, steps, photo_path))
            recipe_id = cursor.lastrowid
            for name, amount, unit in ingredients:
                self.conn.execute(
                    "INSERT OR REPLACE INTO recipe_ingredients (recipe_id, ingredient_id, amount, unit) "
                    "VALUES (?, ?, ?, ?)",
                    (recipe_id, self._ingredient_id(name), amount, unit))
        return recipe_id

    def get_recipe(self, recipe_id: int):
        """Один рецепт вместе с ингредиентами (словарь) или None, если такого нет."""
        row = self.conn.execute("""
            SELECT r.*, COALESCE(c.name, '—') AS category
            FROM recipes r LEFT JOIN categories c ON c.id = r.category_id
            WHERE r.id = ?
        """, (recipe_id,)).fetchone()
        if row is None:
            return None
        recipe = dict(row)
        recipe["ingredients"] = self._ingredients_of(recipe_id)
        return recipe

    def search_recipes(self, text: str = "", category_id=None, max_time: int = 0) -> list[dict]:
        """Рецепты для таблицы главного окна. Условия добавляются только если они заданы.
        max_time == 0 означает «любое время»."""
        sql = """
            SELECT r.id, r.title, r.cook_time, r.servings, COALESCE(c.name, '—') AS category
            FROM recipes r LEFT JOIN categories c ON c.id = r.category_id
            WHERE 1 = 1
        """
        params = []
        if text.strip():
            sql += " AND PYLOWER(r.title) LIKE ?"
            params.append(f"%{text.strip().lower()}%")
        if category_id is not None:
            sql += " AND r.category_id = ?"
            params.append(category_id)
        if max_time:
            sql += " AND r.cook_time <= ?"
            params.append(max_time)
        sql += " ORDER BY PYLOWER(r.title)"
        return [dict(row) for row in self.conn.execute(sql, params)]

    def count_recipes(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]

    # TODO 1 (17 ноября): написать update_recipe(self, recipe_id, title, category_id, cook_time,
    #   servings, steps, photo_path, ingredients).    #   recipe_ingredients этого рецепта и добавить новые, как в add_recipe. Всё внутри `with self.conn:`.
    def update_recipe(self, recipe_id, title, category_id, cook_time, servings, steps,
                      photo_path, ingredients) -> None:
        raise NotImplementedError("Редактирование будет написано на следующем этапе")

    # TODO 2 (17 ноября): написать delete_recipe(self, recipe_id) -> путь к фото или None.
    #   (строки recipe_ingredients удалятся сами из-за ON DELETE CASCADE).
    def delete_recipe(self, recipe_id: int):
        raise NotImplementedError("Удаление будет написано на следующем этапе")


# Форма рецепта
# ======================================================================

class RecipeForm(QDialog):
    """Окно добавления рецепта.

    Наследуется от QDialog: это окно, которое открывается поверх главного и ждёт,
    пока пользователь закроет (метод exec()).
    """

    def __init__(self, db, recipe_id=None, parent=None):
        super().__init__(parent)
        uic.loadUi(str(BASE_DIR / "recipe_form.ui"), self)  # создаёт все виджеты из .ui
        self.db = db
        self.recipe_id = recipe_id        # None - добавляем новый, число - редактируем
        self.new_photo_source = None      # путь к выбранному фото (копируем при сохранении)

        self._setup_widgets()
        self._connect_signals()
        if recipe_id is None:
            self.setWindowTitle("Новый рецепт")
            self.add_ingredient_row()
        else:
            self.setWindowTitle("Редактирование рецепта")
            self._load_recipe()

    # ---------- настройка ----------

    def _setup_widgets(self) -> None:
        for category in self.db.categories():
            self.categoryCombo.addItem(category["name"], category["id"])  # текст + скрытый id
        table = self.ingredientsTable
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Продукт", "Количество", "Единица"])
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

    def _connect_signals(self) -> None:
        """Связываем нажатия кнопок с методами (сигнал clicked -> слот)."""
        self.choosePhotoButton.clicked.connect(self.choose_photo)
        self.removePhotoButton.clicked.connect(self.remove_photo)
        self.addIngredientButton.clicked.connect(lambda: self.add_ingredient_row())
        self.removeIngredientButton.clicked.connect(self.remove_ingredient_row)
        self.saveButton.clicked.connect(self.save)
        self.cancelButton.clicked.connect(self.reject)

    # TODO 4 (17 ноября): заполнить форму данными рецепта для редактирования.
    def _load_recipe(self) -> None:
        raise NotImplementedError("Редактирование будет написано на следующем этапе")

    # ---------- фото ----------

    def _show_photo(self) -> None:
        pixmap = QPixmap(self.new_photo_source) if self.new_photo_source else QPixmap()
        if pixmap.isNull():
            self.photoLabel.setPixmap(QPixmap())
            self.photoLabel.setText("Нет фото")
        else:
            self.photoLabel.setPixmap(pixmap.scaled(
                self.photoLabel.minimumSize(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def choose_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите фото блюда", "", IMAGE_FILTER)
        if path:  # пустая строка значит, что пользователь нажал «Отмена»
            self.new_photo_source = path
            self._show_photo()

    def remove_photo(self) -> None:
        self.new_photo_source = None
        self._show_photo()

    @staticmethod
    def _copy_photo(source: str) -> str:
        """Копирует фото в папку photos и возвращает путь, который запишем в базу."""
        PHOTOS_DIR.mkdir(exist_ok=True)
        name = f"{uuid.uuid4().hex}{Path(source).suffix.lower()}"  # случайное уникальное имя
        shutil.copy2(source, PHOTOS_DIR / name)
        return f"photos/{name}"

    # ---------- таблица ингредиентов ----------

    def add_ingredient_row(self, name: str = "", amount: str = "", unit: str = "г") -> None:
        table = self.ingredientsTable
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(name))
        table.setItem(row, 1, QTableWidgetItem(amount))
        units = QComboBox()          # в ячейку можно положить любой виджет
        units.setEditable(True)
        units.addItems(UNITS)
        units.setCurrentText(unit)
        table.setCellWidget(row, 2, units)

    def remove_ingredient_row(self) -> None:
        row = self.ingredientsTable.currentRow()
        if row >= 0:
            self.ingredientsTable.removeRow(row)

    def _read_ingredients(self):
        """Читает таблицу ингредиентов и проверяет ввод.
        Возвращает список (название, количество, единица) или None, если есть ошибка."""
        table = self.ingredientsTable
        ingredients, seen = [], set()
        for row in range(table.rowCount()):
            name = table.item(row, 0).text().strip()
            amount_text = table.item(row, 1).text().strip().replace(",", ".")
            unit = table.cellWidget(row, 2).currentText().strip()
            if not name and not amount_text:
                continue                                   # пустую строку пропускаем
            if not name:
                self._warn(f"В строке {row + 1} не указан продукт.")
                return None
            try:
                amount = float(amount_text) if amount_text else 0.0
            except ValueError:
                self._warn(f"В строке {row + 1} количество должно быть числом.")
                return None
            if amount < 0:
                self._warn(f"В строке {row + 1} количество не может быть отрицательным.")
                return None
            if normalize(name) in seen:
                self._warn(f"Продукт «{name}» указан дважды.")
                return None
            seen.add(normalize(name))
            ingredients.append((name, amount, unit))
        return ingredients

    # ---------- сохранение ----------

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, "Проверьте данные", text)

    def save(self) -> None:
        """Проверяет форму и записывает рецепт в базу (требование F5 из ТЗ)."""
        title = self.titleEdit.text().strip()
        if not title:
            self._warn("Введите название рецепта.")
            return
        ingredients = self._read_ingredients()
        if ingredients is None:
            return
        if not ingredients:
            self._warn("Добавьте хотя бы один ингредиент.")
            return

        photo = None
        if self.new_photo_source:
            try:
                photo = self._copy_photo(self.new_photo_source)
            except OSError as error:
                self._warn(f"не удалось скопировать фото:\n{error}")
                return

        fields = (title, self.categoryCombo.currentData(), self.timeSpin.value(),
                  self.servingsSpin.value(), self.stepsEdit.toPlainText().strip(), photo, ingredients)
        if self.recipe_id is None:
            self.db.add_recipe(*fields)
        else:
            self.db.update_recipe(self.recipe_id, *fields)
        self.accept()      # закрывает окно с результатом «успешно»


# ======================================================================
# Главное окно

class MainWindow(QMainWindow):
    """Главное окно: таблица рецептов, поиск, фильтры, меню.

    Наследуется от QMainWindow: это окно c готовыми местами под меню (menuBar())
    и строку состояния (statusBar()).
    """

    def __init__(self, db):
        super().__init__()
        uic.loadUi(str(BASE_DIR / "main_window.ui"), self)
        self.db = db
        self._setup_table()
        self._load_categories()
        self._build_menu()
        self._connect_signals()
        self.refresh()

    # ---------- настройка ----------

    def _setup_table(self) -> None:
        table = self.recipesTable
        table.setColumnCount(len(COLUMNS))
        table.setHorizontalHeaderLabels(COLUMNS)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)   # только чтение
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

    def _load_categories(self) -> None:
        self.categoryCombo.addItem("все категории", None)   # None = без фильтра
        for category in self.db.categories():
            self.categoryCombo.addItem(category["name"], category["id"])

    def _add_action(self, menu, text: str, slot, shortcut: str = ""):
        """Создаёт пункт меню и привязывает его к методу."""
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_menu(self) -> None:
        """Меню «Рецепт», «Покупки», «Справка» (F25 в ТЗ)."""
        recipe_menu = self.menuBar().addMenu("Рецепт")
        self._add_action(recipe_menu, "Добавить", self.add_recipe, "Ctrl+N")
        self._add_action(recipe_menu, "Изменить", self.edit_recipe)
        self._add_action(recipe_menu, "Удалить", self.delete_recipe)
        recipe_menu.addSeparator()
        self._add_action(recipe_menu, "Выход", self.close, "Ctrl+Q")

        shop_menu = self.menuBar().addMenu("Покупки")
        self._add_action(shop_menu, "Список покупок", self.open_shopping)
        self._add_action(shop_menu, "Что приготовить?", self.open_finder)

        help_menu = self.menuBar().addMenu("Справка")
        self._add_action(help_menu, "О программе", self.show_about)

    def _connect_signals(self) -> None:
        self.searchEdit.textChanged.connect(self.refresh)
        self.categoryCombo.currentIndexChanged.connect(self.refresh)
        self.timeSlider.valueChanged.connect(self._on_time_changed)
        self.recipesTable.itemSelectionChanged.connect(self._update_buttons)
        self.recipesTable.itemDoubleClicked.connect(lambda _item: self.open_recipe())
        self.addButton.clicked.connect(self.add_recipe)
        self.openButton.clicked.connect(self.open_recipe)
        self.editButton.clicked.connect(self.edit_recipe)
        self.deleteButton.clicked.connect(self.delete_recipe)
        self.finderButton.clicked.connect(self.open_finder)
        self.shoppingButton.clicked.connect(self.open_shopping)
        # Ctrl+F переводит курсор в строку поиска
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(lambda: self.searchEdit.setFocus())
        # Курсор «рука» над кнопками (F28, желательное)
        for button in (self.addButton, self.openButton, self.editButton, self.deleteButton,
                       self.finderButton, self.shoppingButton):
            button.setCursor(Qt.CursorShape.PointingHandCursor)

    # ---------- обработка клавиатуры ----------

    def keyPressEvent(self, event) -> None:
        """Вызывается Qt при нажатии клавиши. Работает, только если фокус в таблице,
        чтобы не мешать вводу текста в строке поиска."""
        if self.recipesTable.hasFocus():
            if event.key() == Qt.Key.Key_Delete:
                self.delete_recipe()
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.open_recipe()
                return
        super().keyPressEvent(event)      # остальные клавиши обрабатываются как обычно

    # ---------- таблица ----------

    def _on_time_changed(self, value: int) -> None:
        self.timeLabel.setText("Любое время" if value == 0 else f"до {value} мин")
        self.refresh()

    def refresh(self) -> None:
        """Перечитывает рецепты из базы с учётом поиска и фильтров и заполняет таблицу."""
        recipes = self.db.search_recipes(
            self.searchEdit.text(),
            self.categoryCombo.currentData(),
            self.timeSlider.value())
        table = self.recipesTable
        table.setRowCount(len(recipes))
        for row, recipe in enumerate(recipes):
            values = [recipe["title"], recipe["category"], recipe["cook_time"], recipe["servings"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 0:
                    # прячем id рецепта в ячейке, чтобы потом узнать, какой рецепт выбран
                    item.setData(Qt.ItemDataRole.UserRole, recipe["id"])
                table.setItem(row, column, item)
        self.statusBar().showMessage(f"Найдено рецептов: {len(recipes)}")
        self._update_buttons()

    def selected_id(self):
        """id выбранного рецепта или None, если ничего не выбрано."""
        items = self.recipesTable.selectedItems()
        if not items:
            return None
        return self.recipesTable.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _update_buttons(self) -> None:
        """Кнопки, которым нужен выбранный рецепт, доступны только при выбранной строке."""
        has_selection = self.selected_id() is not None
        for button in (self.openButton, self.editButton, self.deleteButton):
            button.setEnabled(has_selection)

    # ---------- действия ----------

    def add_recipe(self) -> None:
        form = RecipeForm(self.db, parent=self)
        if form.exec():           # exec() вернёт True, если форму закрыли через accept()
            self.refresh()

    def _not_ready(self, what: str) -> None:
        QMessageBox.information(self, "Скоро", f"«{what}» будет сделано на следующем этапе.")

    # TODO 3 (17 ноября): сделать работающими «Изменить» и «Удалить» (и написать TODO 1, 2, 4).
    def edit_recipe(self) -> None:
        self._not_ready("Редактирование рецепта")

    def delete_recipe(self) -> None:
        self._not_ready("Удаление рецепта")

    def open_recipe(self) -> None:
        self._not_ready("Карточка рецепта")

    def open_shopping(self) -> None:
        self._not_ready("Список покупок")

    def open_finder(self) -> None:
        self._not_ready("Что приготовить?")

    def show_about(self) -> None:
        QMessageBox.about(self, "О программе",
                          "Мой рецептник\nКаталог личных рецептов на Python и PyQt6.")


# ======================================================================
# Запуск
# ======================================================================

def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)

    db = Database()
    window = MainWindow(db)
    window.show()

    exit_code = app.exec()      
    db.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
