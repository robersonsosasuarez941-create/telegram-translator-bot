#!/usr/bin/env python3
"""
Telegram翻译机器人 - 最终优化版
自动将中文/他加禄语翻译成英语
包含HTTP健康检查服务器
"""

import json
import logging
import os
import sys
import time
import asyncio
import re
from datetime import datetime
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from dotenv import load_dotenv
from telegram import Update
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, MessageHandler, filters, CommandHandler, ContextTypes
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==================== 配置部分 ====================

# 加载环境变量
load_dotenv()

# 获取配置
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
TARGET_LANGUAGE = os.getenv("TARGET_LANGUAGE", "en")
HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", "8000"))

# 全局变量
start_time = time.time()
executor = ThreadPoolExecutor(max_workers=5)

# 设置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('translator_bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ==================== 依赖检查 ====================

def check_dependencies():
    """检查必要的依赖是否已安装"""
    required_packages = [
        ('python-telegram-bot', 'telegram'),
        ('requests', 'requests'),
        ('python-dotenv', 'dotenv'),
    ]
    
    missing_packages = []
    for package_name, import_name in required_packages:
        try:
            __import__(import_name)
        except ImportError:
            missing_packages.append(package_name)
    
    if missing_packages:
        print(f"❌ 缺少必要的Python包: {', '.join(missing_packages)}")
        print(f"请使用以下命令安装: pip install {' '.join(missing_packages)}")
        return False
    
    return True

# ==================== HTTP健康检查服务器 ====================

class HealthCheckHandler(BaseHTTPRequestHandler):
    """简单的健康检查处理器"""
    
    def do_GET(self):
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            # 计算运行时间
            uptime = time.time() - start_time
            hours = int(uptime // 3600)
            minutes = int((uptime % 3600) // 60)
            seconds = int(uptime % 60)
            
            response = {
                "status": "healthy",
                "service": "telegram_translator_bot",
                "timestamp": time.time(),
                "uptime": {
                    "hours": hours,
                    "minutes": minutes,
                    "seconds": seconds,
                    "total_seconds": int(uptime)
                }
            }
            
            # 使用json.dumps确保正确的JSON格式
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
        else:
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            error_response = {"error": "Not Found", "path": self.path}
            self.wfile.write(json.dumps(error_response).encode('utf-8'))
    
    def log_message(self, format, *args):
        # 只在调试级别记录HTTP请求
        logger.debug(f"HTTP请求: {self.path} - {args}")

def start_health_server(port: int = 8000) -> HTTPServer:
    """启动HTTP健康检查服务器"""
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        logger.info(f"✅ 健康检查服务器已启动，端口: {port}")
        
        # 在新线程中运行服务器
        def run_server():
            try:
                server.serve_forever()
            except Exception as e:
                logger.error(f"健康检查服务器错误: {e}")
            finally:
                server.server_close()
        
        thread = Thread(target=run_server, daemon=True)
        thread.start()
        return server
    except Exception as e:
        logger.error(f"启动健康检查服务器失败: {e}")
        raise

# ==================== 核心功能 ====================

def translate_with_deepseek(text: str, source_lang_hint: Optional[str] = None) -> Optional[str]:
    """
    使用DeepSeek API翻译文本（同步版本）
    """
    if not text or len(text.strip()) == 0:
        return None
    
    url = "https://api.deepseek.com/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 根据语言提示设置不同的系统提示
    system_prompts = {
        "zh": "你是一位专业的翻译专家。请将以下中文内容准确、自然地翻译成英语。保持原文语气和风格，不要添加额外说明。",
        "tl": "你是一位专业的翻译专家。请将以下他加禄语（Filipino/Tagalog）内容准确翻译成英语。保持原文意思，不要添加额外说明。",
        "auto": "你是一位专业的翻译专家。请将以下内容翻译成英语。如果是混合语言，请整体翻译。只返回翻译结果，不要添加额外说明。"
    }
    
    system_prompt = system_prompts.get(source_lang_hint, system_prompts["auto"])
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请翻译以下内容：{text}"}
        ],
        "temperature": 0.3,
        "max_tokens": 2000
    }
    
    try:
        logger.info(f"调用DeepSeek API翻译: {text[:100]}...")
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        # 检查HTTP状态码
        if response.status_code == 429:
            logger.warning("DeepSeek API速率限制，请稍后重试")
            return None
        
        response.raise_for_status()
        
        result = response.json()
        translated_text = result["choices"][0]["message"]["content"].strip()
        
        # 清理可能的附加说明
        markers = ["翻译：", "Translation:", "翻译结果：", "英文翻译：", "以下是翻译结果："]
        for marker in markers:
            if marker in translated_text:
                translated_text = translated_text.split(marker, 1)[1].strip()
                break
        
        # 移除引号和其他包装字符
        translated_text = translated_text.strip('"\'').strip()
        
        logger.info(f"翻译完成: {text[:50]}... → {translated_text[:50]}...")
        return translated_text
        
    except requests.exceptions.Timeout:
        logger.error("DeepSeek API请求超时")
    except requests.exceptions.RequestException as e:
        logger.error(f"DeepSeek API请求失败: {e}")
    except (KeyError, IndexError) as e:
        logger.error(f"解析API响应失败: {e}")
        if 'response' in locals():
            logger.error(f"API响应内容: {response.text[:500]}")
    except Exception as e:
        logger.error(f"翻译过程未知错误: {e}")
    
    return None

def detect_language_hint(text: str) -> Optional[str]:
    """
    简单语言检测
    """
    if not text:
        return None
    
    # 检测中文字符
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return "zh"
    
    # 检测他加禄语常见词汇
    tagalog_keywords = [
        'ako', 'ikaw', 'siya', 'kami', 'kayo', 'sila',
        'maganda', 'salamat', 'paalam', 'mahal', 'oo', 'hindi',
        'kumusta', 'mabuti', 'pangalan', 'ano', 'saan', 'kailan',
        'po', 'opo', 'hindi po', 'sige', 'tingnan', 'maraming'
    ]
    text_lower = text.lower()
    if any(keyword in text_lower for keyword in tagalog_keywords):
        return "tl"
    
    # 检测英语内容（如果大部分是英语，不翻译）
    english_words = len(re.findall(r'[a-zA-Z]+', text))
    total_words = len(text.split())
    if total_words > 0 and english_words / total_words > 0.7:
        return None  # 主要是英语，不翻译
    
    return None

# ==================== 消息处理 ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    处理收到的消息
    """
    # 跳过空消息
    if not update.message or not update.message.text:
        return
    
    try:
        original_text = update.message.text.strip()
        
        # 跳过短消息和命令
        if len(original_text) < 2 or original_text.startswith('/'):
            return
        
        # 检测语言
        lang_hint = detect_language_hint(original_text)
        
        # 如果检测到中文或他加禄语，进行翻译
        if lang_hint in ["zh", "tl"]:
            logger.info(f"检测到{lang_hint}语言，开始翻译...")
            
            # 发送"正在翻译"提示
            try:
                processing_msg = await update.message.reply_text(
                    "🔄 正在翻译...",
                    reply_to_message_id=update.message.message_id
                )
                has_processing_msg = True
            except Exception as e:
                logger.warning(f"无法发送处理消息: {e}")
                has_processing_msg = False
                processing_msg = None
            
            try:
                # 在线程池中执行同步翻译函数
                loop = asyncio.get_event_loop()
                translated = await loop.run_in_executor(
                    executor,
                    translate_with_deepseek,
                    original_text,
                    lang_hint
                )
                
                # 删除"正在翻译"提示
                if has_processing_msg and processing_msg:
                    try:
                        await processing_msg.delete()
                    except:
                        pass
                
                if translated and translated != original_text:
                    # 发送翻译结果
                    reply_text = f"🌐 翻译成英语:\n\n{translated}"
                    
                    # 回复原消息
                    await update.message.reply_text(
                        reply_text,
                        reply_to_message_id=update.message.message_id,
                        disable_web_page_preview=True
                    )
                    
                    logger.info(f"翻译完成并发送: {original_text[:50]}...")
                elif translated:
                    logger.info("翻译结果与原文相同，跳过发送")
                else:
                    logger.warning("翻译失败，返回None")
                    # 只在群组中发送错误消息，避免私聊骚扰
                    if update.message.chat.type in ['group', 'supergroup']:
                        await update.message.reply_text(
                            "❌ 翻译失败，请稍后重试",
                            reply_to_message_id=update.message.message_id
                        )
                    
            except Exception as e:
                logger.error(f"翻译过程出错: {e}")
                if has_processing_msg and processing_msg:
                    try:
                        await processing_msg.delete()
                    except:
                        pass
                # 只在群组中发送错误消息
                if update.message.chat.type in ['group', 'supergroup']:
                    await update.message.reply_text(
                        "❌ 翻译过程中出现错误",
                        reply_to_message_id=update.message.message_id
                    )
                
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start 命令处理
    """
    await update.message.reply_text(
        "🤖 翻译机器人已启动！\n\n"
        "功能：自动将中文/他加禄语消息翻译成英语\n"
        "支持的语言：中文、菲律宾语（他加禄语）\n"
        "目标语言：英语\n\n"
        "只需在群组中发送消息，机器人会自动检测并翻译。\n\n"
        "命令列表：\n"
        "/start - 启动机器人\n"
        "/help - 显示帮助信息\n"
        "/status - 检查机器人状态"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /help 命令处理
    """
    await update.message.reply_text(
        "📖 使用说明：\n\n"
        "1. 将机器人添加到群组\n"
        "2. 给机器人管理员权限（发送消息）\n"
        "3. 当群组成员发送中文或他加禄语时\n"
        "4. 机器人会自动翻译成英语\n\n"
        "注意：\n"
        "• 只翻译中文和他加禄语到英语\n"
        "• 短消息（<2字符）和命令不会被翻译\n"
        "• 英语内容不会被翻译\n\n"
        "命令列表：\n"
        "/start - 启动机器人\n"
        "/help - 显示帮助信息\n"
        "/status - 检查机器人状态"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /status 命令处理
    """
    uptime = time.time() - start_time
    
    hours = int(uptime // 3600)
    minutes = int((uptime % 3600) // 60)
    seconds = int(uptime % 60)
    
    await update.message.reply_text(
        f"✅ 机器人运行正常！\n\n"
        f"• 运行时间: {hours}小时 {minutes}分钟 {seconds}秒\n"
        f"• 目标语言: 英语\n"
        f"• 支持翻译: 中文 → 英语，他加禄语 → 英语\n"
        f"• 健康检查: ✅ 运行中 (端口 {HEALTH_CHECK_PORT})\n"
        f"• 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"• 日志文件: translator_bot.log"
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    全局错误处理器
    """
    logger.error(f"处理更新时出错: {context.error}")
    
    if update and isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ 处理您的请求时出现错误，请稍后重试"
            )
        except:
            pass

# ==================== 主函数 ====================

def main() -> None:
    """主函数"""
    global start_time
    
    # 检查依赖
    if not check_dependencies():
        sys.exit(1)
    
    # 记录启动时间
    start_time = time.time()
    
    # 检查配置
    if not TELEGRAM_TOKEN:
        logger.error("❌ 未找到 TELEGRAM_TOKEN，请在 .env 文件中设置")
        print("❌ 错误: 需要设置 TELEGRAM_TOKEN")
        print("请创建 .env 文件并添加: TELEGRAM_TOKEN=你的机器人令牌")
        sys.exit(1)
    
    if not DEEPSEEK_API_KEY:
        logger.error("❌ 未找到 DEEPSEEK_API_KEY，请在 .env 文件中设置")
        print("❌ 错误: 需要设置 DEEPSEEK_API_KEY")
        print("请创建 .env 文件并添加: DEEPSEEK_API_KEY=你的DeepSeek API密钥")
        sys.exit(1)
    
    # 显示启动信息
    print("=" * 60)
    print("🤖 Telegram翻译机器人 - 最终优化版")
    print("=" * 60)
    print(f"• Python版本: {sys.version.split()[0]}")
    print(f"• 目标语言: {TARGET_LANGUAGE}")
    print(f"• 健康检查端口: {HEALTH_CHECK_PORT}")
    print(f"• 日志文件: translator_bot.log")
    print("=" * 60)
    
    # 启动健康检查服务器
    try:
        health_server = start_health_server(port=HEALTH_CHECK_PORT)
        print(f"✅ 健康检查服务器已启动")
        print(f"   访问: http://0.0.0.0:{HEALTH_CHECK_PORT}/health")
    except Exception as e:
        logger.error(f"启动健康检查服务器失败: {e}")
        print(f"⚠️  健康检查服务器启动失败: {e}")
        print("⚠️  继续启动机器人，但健康检查不可用...")
    
    print("✅ 配置检查通过")
    print("=" * 60)
    
    try:
        # 创建应用
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # 添加错误处理器
        application.add_error_handler(error_handler)
        
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
        print("🚀 正在启动机器人...")
        print("📱 连接到Telegram服务器...")
        print("=" * 60)
        print("按 Ctrl+C 停止机器人")
        print("=" * 60)
        
        # 启动轮询（带冲突重试机制）
        max_retries = 5
        base_retry_delay = 10  # 秒
        
        for attempt in range(max_retries):
            retry_delay = base_retry_delay * (2 ** attempt)  # 指数退避
            try:
                print(f"🔄 启动尝试 {attempt + 1}/{max_retries}")
                application.run_polling(
                    drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES,
                    close_loop=False
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
                    logger.error(f"启动失败，达到最大重试次数: {e}")
                    raise
                    
            except KeyboardInterrupt:
                print("\n🛑 收到停止信号，正在关闭机器人...")
                print("🔄 清理资源...")
                application.stop()
                executor.shutdown(wait=True)
                print("👋 机器人已停止")
                sys.exit(0)
                
            except NetworkError as e:
                print(f"🌐 网络错误: {e}")
                if attempt < max_retries - 1:
                    print(f"⏳ 等待 {retry_delay} 秒后重试...")
                    time.sleep(retry_delay)
                else:
                    print("❌ 达到最大重试次数，停止尝试")
                    logger.error(f"网络错误，达到最大重试次数: {e}")
                    raise
                    
            except Exception as e:
                print(f"❌ 启动失败: {type(e).__name__}: {e}")
                logger.error(f"启动失败: {e}")
                if attempt < max_retries - 1:
                    print(f"⏳ 等待 {retry_delay} 秒后重试...")
                    time.sleep(retry_delay)
                else:
                    print("❌ 达到最大重试次数，停止尝试")
                    logger.error(f"达到最大重试次数: {e}")
                    raise
        
    except Exception as e:
        logger.error(f"机器人崩溃: {e}")
        print(f"💥 严重错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
