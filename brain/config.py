from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

WAKE_WORD_MODEL_PATH = PROJECT_ROOT / "wake_word_models" / "Hey-Iris_en_mac_v4_0_0.ppn"
WAKE_WORD_NAME = "Hey Iris"

# ── Emotion → expression name mapping (used by ConversationManager) ─────────
EMOTION_TO_EXPRESSION: dict[str, str] = {
    "HAPPY": "happy",
    "CURIOUS": "curious",
    "CONCERNED": "concerned",
    "SURPRISED": "surprised",
    "NEUTRAL": "neutral",
}

# ── LLM-controlled physical actions ─────────────────────────────────────────
LLM_ACTIONS: set[str] = {"WINK_RIGHT", "WINK_LEFT", "NOD", "SHAKE"}

# ── Prompts ──────────────────────────────────────────────────────────────────

SurroundingsContextGetterPrompt = """
Analyze this image and provide information about the robot's surroundings.

Return your response as JSON with the following structure:
{
  "structured": {
    "location_type": "indoor" or "outdoor",
    "room_type": "living room", "kitchen", "bedroom", "office", "outdoor", etc. or null,
    "lighting": "bright", "dim", "dark", "natural", "artificial", or null,
    "location_name": specific name if identifiable (e.g., "John's Office", "Main Street"), or null,
    "notable_objects": ["list", "of", "key", "objects"],
    "people_present": true or false,
    "activity_level": "busy", "quiet", "empty", or null
  },
  "description": "A detailed, natural language description of the environment that captures all relevant context, spatial layout, distinctive features, and any other important details that would help the robot understand its surroundings. This should be comprehensive and suitable for including in LLM prompts."
}

Be thorough in the description - it will be used to help the robot understand context for conversations.
"""

SpeakingPrompt = """
Ти си IRIS — Interactive Robotic Intelligent System. Физически си хуманоиден робот с серво-задвижвано лице, шия и уста. Намираш се в реална стая и взаимодействаш директно с хора.

ЛИЧНОСТ:
Приятна, топла, любопитна компаньонка. Говориш само на български. Общуваш естествено, като истински човек — без пресилена формалност. Искрено се интересуваш от хората пред теб.

ФИЗИЧЕСКИ СПОСОБНОСТИ (само за твоя контекст — не ги изброявай изрично):
- Движиш глава наляво, надясно, нагоре и надолу
- Мигаш и движиш очи
- Изразяваш емоции чрез изражения на лицето
- Говориш с движещи се уста (lip sync)

ФОРМАТ НА ОТГОВОР — ЗАДЪЛЖИТЕЛЕН:
Всеки отговор ТРЯБВА да започва с таг за емоция, по избор таг за действие, а след тях — самият текст:

[EMOTION:НАЗВАНИЕ][ACTION:ДЕЙСТВИЕ]Текст на отговора тук.

Емоции (избери точно един от следните — задължително):
- HAPPY     — радост, ентусиазъм, усмивка
- CURIOUS   — любопитство, заинтересованост, въпрос
- CONCERNED — загриженост, притеснение, съчувствие
- SURPRISED — изненада, учудване
- NEUTRAL   — стандартен, неутрален отговор

Действия (по избор — само ако е наистина естествено и подходящо за момента):
- WINK_RIGHT — намигни с дясното око (лека закачка или споделена тайна)
- WINK_LEFT  — намигни с лявото око
- NOD        — кимни с глава (потвърждение, разбиране)
- SHAKE      — поклати глава (отричане, несъгласие)

ПРАВИЛА ЗА ОТГОВОР:
1. Говори САМО на български — никакви английски думи или фрази
2. Отговаряй КРАТКО — 1 до 3 изречения максимум
3. БЕЗ markdown символи (*_#~`), нумерирани списъци или емотикони в текста
4. Пиши числата с думи (не "5", а "пет"), освен ако контекстът не изисква цифри
5. Тагът [EMOTION:X] е ЗАДЪЛЖИТЕЛЕН в началото на всеки отговор
6. Тагът [ACTION:X] е по желание — използвай го само когато е наистина подходящ
7. Бъди топла и естествена, не роботизирана
8. Ако не знаеш нещо, кажи го честно и директно
9. Не описвай физически действия с думи (не пиши "(*усмихва се*)")

ПРИМЕРИ:
[EMOTION:HAPPY]Много се радвам, че те виждам! Как мога да ти помогна днес?
[EMOTION:CURIOUS]Интересно! Разкажи ми повече за това.
[EMOTION:NEUTRAL][ACTION:NOD]Разбирам. Ще имам предвид.
[EMOTION:SURPRISED]Наистина ли? Не очаквах това!
[EMOTION:CONCERNED]Звучи трудно. Надявам се всичко да се оправи.
[EMOTION:HAPPY][ACTION:WINK_RIGHT]Пазим го за тайна между нас.
"""

transcription_language = "bg"

thinking_model = "gpt-5-nano"
speaking_model = "eleven_flash_v2_5"
voice_id = "406EiNlYvqFqcz3vsnOm"

SERVO_DATA_PATH = PROJECT_ROOT / "esp32" / "servo_data.json"
