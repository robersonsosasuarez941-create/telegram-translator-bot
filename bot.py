import logging
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler
import requests
import json

# 加载环境变量
load_dotenv()

# 获取配置
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TARGET_LANGUAGE = os.getenv("TARGET_LANGUAGE", "en")

# 设置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def translate_with_deepseek(text, source_lang_hint=None):
    """
    使用DeepSeek API翻译文本
    """
    url = "https://api.deepseek.com/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 根据语言提示设置不同的系统提示
    if source_lang_hint == "zh":
        system_prompt = "你是一位专业的翻译专家。请将以下中文内容准确、自然地翻译成英语。保持原文语气和风格。"
    elif source_lang_hint == "tl":
        system_prompt = "你是一位专业的翻译专家。请将以下他加禄语（Filipino/Tagalog）内容准确翻译成英语。保持原文意思。"
    else:
        system_prompt = "你是一位专业的翻译专家。请将以下内容翻译成英语。如果是混合语言，请整体翻译。"
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请翻译以下内容：{text}"}
        ],
        "temperature": 0.3,
        "max_tokens": 1000
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        translated_text = result["choices"][0]["message"]["content"].strip()
        
        # 清理可能的附加说明
        if "翻译：" in translated_text:
            translated_text = translated_text.split("翻译：", 1)[1].strip()
        elif "Translation:" in translated_text:
            translated_text = translated_text.split("Translation:", 1)[1].strip()
        
        return translated_text
        
    except Exception as e:
        logger.error(f"翻译失败: {e}")
        return None

def detect_language_hint(text):
    """
    简单语言检测
    """
    # 检测中文字符
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return "zh"
    
    # 检测他加禄语常见词汇
    tagalog_keywords = [
        'ako', 'ikaw', 'siya', 'kami', 'kayo', 'sila',
        'maganda', 'salamat', 'paalam', 'mahal', 'oo', 'hindi',
        'kumusta', 'mabuti', 'pangalan', 'ano', 'saan', 'kailan'
    ]
    text_lower = text.lower()
    if any(keyword in text_lower for keyword in tagalog_keywords):
        return "tl"
    
    return None

async def handle_message(update: Update, context):
    """
    处理收到的消息
    """
    # 跳过空消息和命令
    if not update.message or not update.message.text:
        return
    
    original_text = update.message.text.strip()
    
    # 跳过短消息和命令
    if len(original_text) < 2 or original_text.startswith('/'):
        return
    
    # 检测语言
    lang_hint = detect_language_hint(original_text)
    
    # 如果检测到中文或他加禄语，进行翻译
    if lang_hint in ["zh", "tl"]:
        logger.info(f"检测到{lang_hint}语言，开始翻译...")
        
        # 调用翻译
        translated = translate_with_deepseek(original_text, lang_hint)
        
        if translated and translated != original_text:
            # 发送翻译结果
            reply_text = f"🌐 翻译成英语:\n{translated}"
            
            # 可选：回复原消息
            await update.message.reply_text(
                reply_text,
                reply_to_message_id=update.message.message_id
            )
            
            logger.info(f"翻译完成: {original_text[:50]}... → {translated[:50]}...")

async def start_command(update: Update, context):
    """
    /start 命令处理
    """
    await update.message.reply_text(
        "🤖 翻译机器人已启动！\n\n"
        "功能：自动将中文/他加禄语消息翻译成英语\n"
        "支持的语言：中文、菲律宾语（他加禄语）\n"
        "目标语言：英语\n\n"
        "只需在群组中发送消息，机器人会自动检测并翻译。"
    )

async def help_command(update: Update, context):
    """
    /help 命令处理
    """
    await update.message.reply_text(
        "📖 使用说明：\n\n"
        "1. 将机器人添加到群组\n"
        "2. 给机器人管理员权限（发送消息）\n"
        "3. 当群组成员发送中文或他加禄语时\n"
        "4. 机器人会自动翻译成英语\n\n"
        "命令列表：\n"
        "/start - 启动机器人\n"
        "/help - 显示帮助信息\n"
        "/status - 检查机器人状态"
    )

async def status_command(update: Update, context):
    """
    /status 命令处理
    """
    await update.message.reply_text(
        "✅ 机器人运行正常！\n"
        f"目标语言：英语\n"
        f"支持翻译：中文 → 英语，他加禄语 → 英语"
    )

def main():
    """主函数"""
    # 检查配置
    if not TELEGRAM_TOKEN:
        logger.error("未找到 TELEGRAM_TOKEN，请在 .env 文件中设置")
        return
    
    if not DEEPSEEK_API_KEY:
        logger.error("未找到 DEEPSEEK_API_KEY，请在 .env 文件中设置")
        return
    
    # 创建应用
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # 添加命令处理器
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    
    # 添加消息处理器（排除命令）
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    ))
    
    # 启动机器人
    logger.info("🤖 机器人启动中...")
    print("=" * 50)
    print("🤖 Telegram翻译机器人")
    print(f"目标语言：英语")
    print("支持：中文 → 英语，他加禄语 → 英语")
    print("=" * 50)
    print("按 Ctrl+C 停止机器人")
    print("=" * 50)
    
    # 开始轮询
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
