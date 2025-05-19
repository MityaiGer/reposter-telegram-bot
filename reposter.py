#reposter.py
import asyncio
import random
import time
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from collections import deque
from aiogram.dispatcher import FSMContext
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from config import SOURCE_CHANNELS, TARGET_CHANNELS, API_KEY, MODERN_SOURCE_CHANNELS

bot = Bot(token=API_KEY)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

# Очередь для целевых каналов
target_channel_queue = deque(TARGET_CHANNELS)

last_published_posts_target= {target_channel_id: deque(maxlen=10) for target_channel_id in TARGET_CHANNELS}
last_published_posts_source= {source_channel_id: deque(maxlen=10) for source_channel_id in MODERN_SOURCE_CHANNELS}

# Словарь для отслеживания времени последнего репоста на каждом целевом канале
last_repost_times = {channel: None for channel in TARGET_CHANNELS}

# Словарь для отслеживания последних репостов с каждого исходного канала на целевые каналы
last_reposts_from_source = {source_channel_id: {target_channel_id: None for target_channel_id in TARGET_CHANNELS} for source_channel_id in SOURCE_CHANNELS}
last_reposts_to_source = {target_channel_id: {source_channel_id: None for source_channel_id in MODERN_SOURCE_CHANNELS} for target_channel_id in TARGET_CHANNELS}

REPOST_INTERVAL = 3 # 60 мин
# Глобальный список всех доступных каналов
all_channels = {channel_id: f"Канал {i+1}" for i, channel_id in enumerate(MODERN_SOURCE_CHANNELS)}      
start_keyboard = ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("/start")).add(KeyboardButton("/stop"))

delays_list = []
media_group_info = {}

selected_channels = set()
paused_channels = set()
skipped_posts = set()
skipped_message_ids = set()

delay_min = 30
delay_max = 220

posted_to_channels = set()
published_to_channels_current_run = set()

duplicate_message_ids = []

repost_links_main = []
repost_links_target = []
published_links_source = []
published_links_target =[]

continue_reposting = True
stop_event = asyncio.Event()

class UserState(StatesGroup):
    waiting_for_start = State()
    waiting_for_text = State()
    delay = State()
    choose_channels = State()

channels_keyboard_markup = ReplyKeyboardMarkup(resize_keyboard=True)
channels_keyboard_markup.row(KeyboardButton("Да"), KeyboardButton("Нет"))


channel_names = {
    -1000000000000: "Source 2",
    -1000000000001: "Source 1",
    # Добавьте остальные каналы
}

# Добавьте функцию для определения, нужно ли репостить в данный момент времени
def should_repost_now():
    now = datetime.now()

    # Проверьте, находится ли текущее время вне указанных временных интервалов
    if (now.hour >= 0 and now.hour < 6):
        return False
    return True

def should_skip_repost_target(target_channel_id, post_text):
    global continue_reposting
    continue_reposting = True
    for last_post_text in last_published_posts_target[target_channel_id]:
        if post_text == last_post_text:
            return True
    return False

def get_random_delay_between_posts(min_delay, max_delay):
    return random.uniform(min_delay, max_delay)

def add_channel_to_queue_with_delay(channel_id, min_delay, max_delay):
    delay = get_random_delay_between_posts(min_delay, max_delay)
    time.sleep(delay)
    add_channel_to_queue(channel_id)
    last_repost_times[channel_id] = datetime.now()

# Функция для добавления канала в очередь и обновления времени последнего репоста
def add_channel_to_queue(channel_id):
    target_channel_queue.append(channel_id)
    last_repost_times[channel_id] = datetime.now()
    random.shuffle(TARGET_CHANNELS)

# Функция для выбора следующего канала из очереди с учетом таймаутов
def get_next_channels(current_target_channels):
    if not current_target_channels:
        # Если список целевых каналов исчерпан, перемешайте его и возобновите
        current_target_channels.extend(TARGET_CHANNELS)
        

    num_channels = 4  # Случайное количество каналов (2 или 3)
    selected_channels = []

    while target_channel_queue and num_channels > 0:
        channel_id = target_channel_queue.popleft()
        last_repost_time = last_repost_times[channel_id]
        if last_repost_time is None or (datetime.now() - last_repost_time).total_seconds() >= REPOST_INTERVAL:
            selected_channels.append(channel_id)
            num_channels -= 1
        else:
            # Если канал не подходит из-за таймаута, добавляем его в конец очереди и пробуем следующий
            target_channel_queue.append(channel_id)

    return selected_channels

def restore_target_channel_state():
    global target_channel_queue
    target_channel_queue.extend(paused_channels)
            
async def return_channels_to_queue():
    while True:
        await asyncio.sleep(2)  # Подождите 5 минут перед возвращением каналов в очередь
        for channel_id, last_repost_time in last_repost_times.items():
            if last_repost_time is not None and (datetime.now() - last_repost_time).total_seconds() >= REPOST_INTERVAL:
                add_channel_to_queue(channel_id)

async def repost_to_target_channel(source_channel_id, target_channel_id, message_id, post_text):
    try:
        # Проверка, совпадает ли текст поста с каким-либо из последних 5 опубликованных постов для целевого канала
        if should_skip_repost_target(target_channel_id, post_text):
            
            print(f"Пост с текстом '{post_text}' уже был репостнут в {target_channel_id}. Пропускаем.")
            return
                   
        if last_published_posts_source[source_channel_id] and post_text == last_published_posts_source[source_channel_id][0]:
            
            await bot.forward_message(f"Пост с таким тектом уже публиковался на канале: {source_channel_id}")
            print(f"Пост с текстом '{post_text}' уже был репостнут в основной канал. Пропускаем.")
            return
        
        
        await bot.forward_message(target_channel_id, source_channel_id, message_id)
        add_channel_to_queue(target_channel_id)
        print(f"Репост с {source_channel_id} в {target_channel_id} отправлен")
        # Обновление времени последнего репоста для целевого канала
        last_reposts_from_source[source_channel_id][target_channel_id] = datetime.now()
        # Обновление последних 5 опубликованных постов для целевого канала
        last_published_posts_source[source_channel_id].appendleft(post_text)
        last_published_posts_target[target_channel_id].appendleft(post_text)
        
        published_to_channels_current_run.add(target_channel_id)
    except Exception as e:
        print(f"Ошибка при репосте: {e}")
#start_keyboard = ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("Старт"))


      

@dp.channel_post_handler(content_types=types.ContentType.ANY, chat_id=SOURCE_CHANNELS)
async def repost_to_target_channels(message: types.Message):
    
    source_channel_id = message.chat.id
    message_id = message.message_id
    post_text = message.text or message.caption or ""

    if source_channel_id not in last_published_posts_source:
        last_published_posts_source[source_channel_id] = deque(maxlen=10)
                
    # Проверьте, нужно ли сейчас делать репост
    if not should_repost_now():
        print("Не пересылаем в данный момент времени.")
        return

    if message.text is not None and "#РепостОтДрузей" in message.text:
        print(f"На канале {source_channel_id} обнаружена реклама. Пост пропущен.")
        return

    if message.caption is not None and "#РепостОтДрузей" in message.caption:
        print(f"На канале {source_channel_id} обнаружена реклама. Пост пропущен.")
        return

    if message.photo and not message.caption:
        print(f"Пост из {source_channel_id} пропущен из-за отсутствия caption.")
        return
    
    if message.media_group_id and not message.caption:
        print(f"Пост из {source_channel_id} пропущен из-за отсутствия caption_media.")
        return

    # Получите случайное количество (2 или 3) доступных целевых каналов
    target_channel_ids = get_next_channels(TARGET_CHANNELS)
    
    if target_channel_ids:
        for target_channel_id in target_channel_ids:
            # Проверьте, был ли уже репост с этого исходного канала на целевой канал в течение последних 2 часов
            if last_reposts_from_source[source_channel_id][target_channel_id] is not None and (datetime.now() - last_reposts_from_source[source_channel_id][target_channel_id]).total_seconds() < 1:
                
                add_channel_to_queue(target_channel_id)
                print(f"Пост из {source_channel_id} уже был переслан на {target_channel_id} в течение последних 2 часов. Пропускаем.")
                continue
            
            if should_skip_repost_target(target_channel_id, post_text):
                post_link_target = f"https://t.me/{(await bot.get_chat(target_channel_id)).username}"
                published_links_target.append(post_link_target)
                add_channel_to_queue(target_channel_id)
                print(f"Пост с {source_channel_id} с текстом '{post_text}' уже был репостнут в {target_channel_id}. Пропускаем.")
                continue
            await repost_to_target_channel(source_channel_id, target_channel_id, message_id, post_text)
            
    else:
        
        print("В данный момент нет доступных каналов для пересылки.")
    
@dp.message_handler(commands=['start'], state='*')
async def handle_start(message: types.Message, state: FSMContext):
    global delay_min, delay_max
    global continue_reposting
    continue_reposting = True
    
    await UserState.waiting_for_start.set()
    await message.reply(f"👋 Привет! Отправьте мне ваш пост, и я перешлю его в целевые каналы.\n\n"
                        f"🕒 Текущие минимальное и максимальное время задержки:\n\n📍 Период от {delay_min} до {delay_max} секунд.\n\n"
                        f"✍️ Для изменения времени задержки, используйте команду /delay\n\n"
                        f"⚠️ ПРИМЕР-1- /delay 60 180\n\n"
                        f"🚀 ПРИМЕР-2- /delay 0 0 (БЫСТРАЯ)",
                        reply_markup=start_keyboard and channels_keyboard_markup )

@dp.message_handler(lambda message: message.text.lower() == 'да', state=UserState.waiting_for_start)
async def handle_choose_channels_yes(message: types.Message, state: FSMContext):
    await UserState.choose_channels.set()
    
    # Создаем клавиатуру с кнопками для каждого канала
    channels_keyboard_markup = ReplyKeyboardMarkup(resize_keyboard=True)
    for channel_id, channel_name in all_channels.items():
        channels_keyboard_markup.row(KeyboardButton(channel_name))
    
    # Добавляем кнопку "Завершить"
    channels_keyboard_markup.row(KeyboardButton("Завершить"))
    
    await message.reply("Выберите каналы для рассылки из списка:", reply_markup=channels_keyboard_markup)

@dp.message_handler(lambda message: message.text.lower() == 'нет', state=UserState.waiting_for_start)
async def handle_choose_channels_no(message: types.Message, state: FSMContext):
    # Продолжаем без выбора каналов
    await state.update_data(selected_channels=MODERN_SOURCE_CHANNELS)
    await UserState.waiting_for_text.set()
    await message.reply("Рассылка будет производиться на все каналы. Теперь отправьте мне ваш пост.", reply_markup=start_keyboard)

@dp.message_handler(lambda message: message.text.lower() == 'завершить', state=UserState.choose_channels)
async def handle_finish_channels(message: types.Message, state: FSMContext):
    # Завершаем выбор каналов и переходим к следующему состоянию
    await state.update_data(selected_channels=list(selected_channels))
    await UserState.waiting_for_text.set()

    # Сбрасываем список выбранных каналов

    
    await message.reply("Теперь отправьте мне ваш пост.", reply_markup=start_keyboard)
    selected_channels.clear()

@dp.message_handler(lambda message: message.text in all_channels.values(), state=UserState.choose_channels)
async def handle_select_channel(message: types.Message, state: FSMContext):
    global selected_channels
    channel_name = message.text
    channel_id = next(key for key, value in all_channels.items() if value == channel_name)
    
    # Добавляем канал в список выбранных
    selected_channels.add(channel_id)
    
    # Отправляем уведомление о добавлении канала
    await message.reply(f"Канал {channel_name} добавлен в список для рассылки.")
    
    # Возвращаемся к выбору каналов
    await UserState.choose_channels.set()
    
    

@dp.message_handler(lambda message: message.text.lower() == '/start', state=UserState.waiting_for_start)
async def handle_start_button(message: types.Message, state: FSMContext):
    await UserState.waiting_for_start.set()
    await message.reply("Теперь отправьте мне ваш пост.", reply_markup=start_keyboard)
    
    
@dp.message_handler(content_types=types.ContentType.ANY, state=None)
async def handle_post_without_start(message: types.Message, state: FSMContext):
    # Пользователь пытается отправить пост до нажатия "Старт"

    await message.reply("Чтобы отправить пост, сначала нажмите на кнопку /start",reply_markup=start_keyboard)

    
@dp.message_handler(commands=['stop'], state='*')
async def stop_reposting(message: types.Message, state: FSMContext):
    global continue_reposting
    continue_reposting = False
    stop_event.set()
    await message.reply("⛔️ Репосты остановлены. Для возобновления используйте /start",reply_markup=start_keyboard)



@dp.message_handler(commands=['delay'], state='*')
async def handle_set_delay(message: types.Message, state: FSMContext):
    global delay_min, delay_max
    try:
        command, new_min, new_max = message.text.split()
        new_min, new_max = int(new_min), int(new_max)
        
        delay_min = new_min
        delay_max = new_max

        await message.reply(f"🕰 Минимальное и максимальное время задержки обновлены:\n\n📍 Период от {new_min} до {new_max} секунд.\n\n"
                            f"👇 Отправьте мне ваш пост, и я пересылю его в целевые каналы.\n"
                            f"⚠️ Или еще раз измените время - /delay 60 180", reply_markup=start_keyboard)
    except ValueError:
        await message.reply("Некорректный формат команды. Используйте /delay минимальное_время максимальное_время.")

        
@dp.message_handler(content_types=types.ContentType.ANY, state='*')
async def handle_post(message: types.Message, state: FSMContext,):
    await UserState.waiting_for_start.set()
    global continue_reposting
    continue_reposting = True
    global stop_event
    stop_event = asyncio.Event()
    global published_to_channels_current_run
    published_to_channels_current_run = set()
    global repost_links_main, repost_links_target
    source_channel_id = message.chat.id
    message_id = message.message_id
    post_text = message.text or message.caption or ""
    
                
            
    # Проверьте, нужно ли сейчас делать репост
    if not should_repost_now():
        print("Не пересылаем в данный момент времени.")
        return

    if message.text is not None and "#РепостОтДрузей" in message.text:
        print(f"На канале {source_channel_id} обнаружена реклама. Пост пропущен.")
        return

    if message.caption is not None and "#РепостОтДрузей" in message.caption:
        print(f"На канале {source_channel_id} обнаружена реклама. Пост пропущен.")
        return

    if message.photo and not message.caption:
        print(f"Пост из {source_channel_id} пропущен из-за отсутствия caption.")
        return

    if message.media_group_id and not message.caption:
        print(f"Пост из {source_channel_id} пропущен из-за отсутствия caption_media.")
        return
    
    # Проверяем, что сообщение отправлено пользователем (а не каналом или ботом)
    if message.from_user is not None:
        post_text = message.text or message.caption or ""



    for source_channel_id in MODERN_SOURCE_CHANNELS:
        
        try:   
            if stop_event.is_set():
                
                print("⛔️ Репосты остановлены.")

                break

                
            if source_channel_id not in last_published_posts_source:
                last_published_posts_source[source_channel_id] = deque(maxlen=10)
                


            if last_published_posts_source[source_channel_id] and post_text == last_published_posts_source[source_channel_id][0]:
                await message.reply(f"❗️ Пост с таким тектом уже публиковался на канале: {source_channel_id}")
                print(f"❗️ Пост с текстом '{post_text}' уже был репостнут в основной канал. Пропускаем.")
                continue  
            
            forward_message = await bot.forward_message(source_channel_id, message.chat.id, message.message_id)
            message_id = forward_message.message_id
            repost_link = f"https://t.me/{(await bot.get_chat(source_channel_id)).username}/{message_id}"
            repost_links_main.append(repost_link)
            
            await message.reply(f"✅ Ваш пост был переслан в ОСНОВНОЙ канал: {source_channel_id}")
            
            target_channel_ids = get_next_channels(TARGET_CHANNELS)
            last_published_posts_source[source_channel_id].appendleft(post_text)
            
            random_delay = random.randint(delay_min, delay_max)
            await bot.send_message(message.chat.id, f'⏳ Следущий Репост будет отправлен через {random_delay} секунд. Ожидайте...')
            await asyncio.sleep(random_delay)
            
            if target_channel_ids: 
                for target_channel_id in target_channel_ids:
                       
                        
                    if last_reposts_from_source[source_channel_id][target_channel_id] is not None and (datetime.now() - last_reposts_from_source[source_channel_id][target_channel_id]).total_seconds() < 1:
                        add_channel_to_queue(target_channel_id)
                        print(f"❗️ Пост из {source_channel_id} уже был переслан на {target_channel_id} в течение последних 2 часов. Пропускаем.")
                        continue
                    
                    
                    if should_skip_repost_target(target_channel_id, post_text):
                        add_channel_to_queue(target_channel_id)
                        print(f"❗️ Пост с {source_channel_id} с текстом '{post_text}' уже был репостнут в {target_channel_id}. Пропускаем.")
                        continue   
                    
                    if stop_event.is_set():
                        
                        print("⛔️ Репосты остановлены.")
                        add_channel_to_queue(target_channel_id)
                        await message.reply("\n".join(["ССЫЛКИ:"] + repost_links_main +[""] + 
                                    repost_links_target + [""]+ 
                                    ["\n📣 Репост завершен.\n\n⭐️ ДЛЯ ПРОДОЛЖЕНИЯ НАЖМИТЕ НА /start"]))
                        last_reposts_from_source[source_channel_id][target_channel_id] = datetime.now()
                        await state.finish()
                        repost_links_main.clear()
                        repost_links_target.clear()
                        break         
                    forward_message_target = await bot.forward_message(target_channel_id, message.chat.id, message.message_id)
                    message_id_target = forward_message_target.message_id
                    repost_link_target = f"https://t.me/{(await bot.get_chat(target_channel_id)).username}/{message_id_target}"
                    repost_links_target.append(repost_link_target)
                    await message.reply(f"✅ Ваш пост был переслан в ЦЕЛЕВОЙ канал: {target_channel_id}")
                    add_channel_to_queue(target_channel_id)
                    print(f"Репост с {source_channel_id} в {target_channel_id} отправлен")
                    # Обновление времени последнего репоста для целевого канала
                    last_reposts_from_source[source_channel_id][target_channel_id] = datetime.now()
                    
                    # Обновление последних 5 опубликованных постов для целевого канала
                    last_published_posts_target[target_channel_id].appendleft(post_text)
                    random_delay = random.randint(delay_min, delay_max)
                    await bot.send_message(message.chat.id, f'⏳ Следущий Репост будет отправлен через {random_delay} секунд. Ожидайте...')
                    await asyncio.sleep(random_delay)
                    
                

        except Exception as e:
            print(f"Ошибка при репосте: {e}")
    
                
    await message.reply("\n".join(["ССЫЛКИ:"] + repost_links_main +[""] + 
                                repost_links_target + [""]+ 
                                  ["\n📣 Репост завершен.\n\n⭐️ ДЛЯ ПРОДОЛЖЕНИЯ НАЖМИТЕ НА /start"]))
    await state.finish()
    repost_links_main.clear()
    repost_links_target.clear() 
            
    

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.create_task(dp.start_polling())
        executor.start_polling(dp)
        loop.run_forever()
    finally:
        loop.stop()
        loop.run_until_complete(dp.stop_polling())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()