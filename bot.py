"""
Telegram翻译机器人 - 多语言版本
支持：中文→乌尔都语，他加禄语→英语
添加HTTP健康检查服务器
"""

import logging
import os
import sys
import time
import asyncio
from threading import Thread
from dotenv import load_dotenv
from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, MessageHandler, filters, CommandHandler
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==================== 配置部分 ====================

# 加载环境变量
load_dotenv()

# 获取配置
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
TARGET_LANGUAGE = os.getenv("TARGET_LANGUAGE", "en")

# 设置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== HTTP健康检查服务器 ====================

class HealthCheckHandler(BaseHTTPRequestHandler):
    """简单的健康检查处理器"""
    
    def do_GET(self):
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK - Telegram Translator Bot is running')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # 减少日志输出
        pass

def start_health_server(port=8000):
    """启动HTTP健康检查服务器"""
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"✅ 健康检查服务器已启动，端口: {port}")
    
    # 在新线程中运行服务器
    def run_server():
        server.serve_forever()
    
    thread = Thread(target=run_server, daemon=True)
    thread.start()
    return server

# ==================== 核心功能 ====================

def translate_with_deepseek(text, source_lang_hint=None, target_lang=None):
    """
    使用DeepSeek API翻译文本
    参数:
        text: 要翻译的文本
        source_lang_hint: 源语言提示 ('zh'=中文, 'tl'=他加禄语, 'ur'=乌尔都语)
        target_lang: 目标语言 ('ur'=乌尔都语, 'en'=英语)
    """
    url = "https://api.deepseek.com/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 根据语言提示和目标语言设置不同的系统提示
    if source_lang_hint == "zh" and target_lang == "ur":
        # 中文 -> 乌尔都语
        system_prompt = "你是一位专业的翻译专家。请将以下中文内容准确、自然地翻译成乌尔都语（Urdu）。保持原文语气和风格。"
        user_prompt = f"请将以下中文翻译成乌尔都语：{text}"
        
    elif source_lang_hint == "tl" and target_lang == "en":
        # 他加禄语 -> 英语
        system_prompt = "你是一位专业的翻译专家。请将以下他加禄语（Filipino/Tagalog）内容准确翻译成英语。保持原文意思。"
        user_prompt = f"请将以下他加禄语翻译成英语：{text}"
        
    elif source_lang_hint == "ur" and target_lang == "en":
        # 乌尔都语 -> 英语（备用，如果检测到乌尔都语但用户想翻译成英语）
        system_prompt = "你是一位专业的翻译专家。请将以下乌尔都语（Urdu）内容准确翻译成英语。保持原文意思。"
        user_prompt = f"请将以下乌尔都语翻译成英语：{text}"
        
    else:
        # 默认：翻译成英语
        system_prompt = "你是一位专业的翻译专家。请将以下内容翻译成英语。如果是混合语言，请整体翻译。"
        user_prompt = f"请翻译以下内容：{text}"
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
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
        for prefix in ["翻译：", "Translation:", "乌尔都语翻译：", "英语翻译："]:
            if prefix in translated_text:
                translated_text = translated_text.split(prefix, 1)[1].strip()
        
        return translated_text
        
    except requests.exceptions.Timeout:
        logger.error("DeepSeek API请求超时")
    except requests.exceptions.RequestException as e:
        logger.error(f"DeepSeek API请求失败: {e}")
    except (KeyError, IndexError) as e:
        logger.error(f"解析API响应失败: {e}")
    except Exception as e:
        logger.error(f"翻译过程未知错误: {e}")
    
    return None

def detect_language_hint(text):
    """
    简单语言检测
    返回: 'zh'(中文), 'tl'(他加禄语), 'ur'(乌尔都语), 或 None
    """
    # 检测中文字符（Unicode范围）
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
    
    # 检测乌尔都语字符（阿拉伯文字符范围）
    # 乌尔都语使用阿拉伯文字符，Unicode范围：\u0600-\u06FF
    if any('\u0600' <= char <= '\u06FF' for char in text):
        return "ur"
    
    return None

# ==================== 消息处理 ====================

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
    
    # 根据检测到的语言选择翻译目标
    if lang_hint == "zh":
        # 中文 -> 乌尔都语
        logger.info(f"检测到中文，开始翻译成乌尔都语...")
        translated = translate_with_deepseek(original_text, "zh", "ur")
        target_lang_name = "乌尔都语"
        
    elif lang_hint == "tl":
        # 他加禄语 -> 英语
        logger.info(f"检测到他加禄语，开始翻译成英语...")
        translated = translate_with_deepseek(original_text, "tl", "en")
        target_lang_name = "英语"
        
    elif lang_hint == "ur":
        # 乌尔都语 -> 英语（或者您可以改成乌尔都语->中文）
        logger.info(f"检测到乌尔都语，开始翻译成英语...")
        translated = translate_with_deepseek(original_text, "ur", "en")
        target_lang_name = "英语"
        
    else:
        # 未检测到支持的语言，不翻译
        return
    
    if translated and translated != original_text:
        # 发送翻译结果
        reply_text = f"🌐 翻译成{target_lang_name}:\n{translated}"
        
        # 回复原消息
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
        "🤖 多语言翻译机器人已启动！\n\n"
        "✨ 功能特性：\n"
        "• 自动将中文消息翻译成乌尔都语\n"
        "• 自动将他加禄语消息翻译成英语\n"
        "• 支持乌尔都语消息翻译成英语\n"
        "• 群组自动翻译，无需命令\n\n"
        "🌐 支持的语言：\n"
        "• 中文 (Chinese)\n"
        "• 他加禄语/菲律宾语 (Tagalog/Filipino)\n"
        "• 乌尔都语 (Urdu)\n\n"
        "🎯 目标语言：\n"
        "• 乌尔都语 (Urdu)\n"
        "• 英语 (English)\n\n"
        "📝 使用方法：\n"
        "只需在群组中发送消息，机器人会自动检测并翻译。"
    )

async def help_command(update: Update, context):
    """
    /help 命令处理
    """
    await update.message.reply_text(
        "📖 详细使用说明\n\n"
        "🔄 翻译规则：\n"
        "• 中文 → 乌尔都语\n"
        "• 他加禄语 → 英语\n"
        "• 乌尔都语 → 英语\n\n"
        "👥 群组设置：\n"
        "1. 将机器人添加到群组\n"
        "2. 给机器人管理员权限（发送消息）\n"
        "3. 关闭隐私模式 (@BotFather设置)\n"
        "4. 在群组中正常聊天即可\n\n"
        "🔧 可用命令：\n"
        "/start - 显示机器人信息\n"
        "/help - 显示帮助信息\n"
        "/status - 检查机器人状态\n"
        "/languages - 查看支持的语言"
    )

async def status_command(update: Update, context):
    """
    /status 命令处理
    """
    await update.message.reply_text(
        "✅ 机器人运行正常！\n\n"
        "🌐 当前翻译配置：\n"
        "• 中文 → 乌尔都语\n"
        "• 他加禄语 → 英语\n"
        "• 乌尔都语 → 英语\n\n"
        "📊 系统状态：\n"
        "• 健康检查：✅ 运行中 (端口 8000)\n"
        "• 部署平台：Koyeb Cloud\n"
        "• 运行实例：Free (0.1 vCPU, 512MB RAM)"
    )

async def languages_command(update: Update, context):
    """
    /languages 命令 - 查看支持的语言
    """
    await update.message.reply_text(
        "🌍 支持的语言列表：\n\n"
        "📥 输入语言：\n"
        "• 中文 (Chinese) - 自动检测中文字符\n"
        "• 他加禄语 (Tagalog) - 检测常见词汇\n"
        "• 乌尔都语 (Urdu) - 检测阿拉伯文字符\n\n"
        "📤 输出语言：\n"
        "• 乌尔都语 (Urdu) - 用于中文翻译\n"
        "• 英语 (English) - 用于他加禄语和乌尔都语翻译\n\n"
        "🔀 翻译方向：\n"
        "中文 → 乌尔都语\n"
        "他加禄语 → 英语\n"
        "乌尔都语 → 英语"
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
    
    # 启动健康检查服务器
    print("=" * 60)
    print("🤖 Telegram多语言翻译机器人")
    print("支持：中文→乌尔都语，他加禄语→英语")
    print("=" * 60)
    
    try:
        health_server = start_health_server(port=8000)
        print("✅ 健康检查服务器已启动 (端口: 8000)")
        print(f"   访问: http://0.0.0.0:8000/health")
    except Exception as e:
        logger.error(f"启动健康检查服务器失败: {e}")
        print("⚠️  健康检查服务器启动失败，但继续启动机器人...")
    
    print(f"✅ 配置检查通过")
    print(f"• 翻译规则: 中文 → 乌尔都语，他加禄语 → 英语")
    print("=" * 60)
    
    try:
        # 创建应用
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # 添加命令处理器
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(CommandHandler("languages", languages_command))
        
        # 添加消息处理器（排除命令）
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        ))
        
        # 启动机器人
        logger.info("🤖 机器人启动中...")
        print("🚀 正在启动机器人...")
        print("📱 连接到Telegram服务器...")
        print("=" * 60)
        print("按 Ctrl+C 停止机器人")
        print("=" * 60)
        
        # 启动轮询（带冲突重试机制）
        max_retries = 5
        retry_delay = 10  # 秒
        
        for attempt in range(max_retries):
            try:
                print(f"🔄 启动尝试 {attempt + 1}/{max_retries}")
                application.run_polling(
                    drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES
                )
                print("✅ 机器人正常停止")
                break  # 如果成功运行后停止，跳出循环
                
            except Conflict as e:
                print(f"⚠️ 检测到冲突错误: {e}")
                if attempt < max_retries - 1:
                    print(f"⏳ 等待 {retry_delay} 秒后重试...")
                    time.sleep(retry_delay)
                else:
                    print("❌ 达到最大重试次数，停止尝试")
                    raise
                    
            except KeyboardInterrupt:
                print("\n🛑 收到停止信号，正在关闭机器人...")
                application.stop()
                print("👋 机器人已停止")
                sys.exit(0)
                
            except Exception as e:
                print(f"❌ 启动失败: {e}")
                logger.error(f"启动失败: {e}")
                if attempt < max_retries - 1:
                    print(f"⏳ 等待 {retry_delay} 秒后重试...")
                    time.sleep(retry_delay)
                else:
                    print("❌ 达到最大重试次数，停止尝试")
                    raise
        
    except Exception as e:
        logger.error(f"机器人崩溃: {e}")
        print(f"💥 严重错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
