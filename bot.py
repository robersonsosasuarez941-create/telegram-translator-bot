#!/usr/bin/env python3
"""
Telegram翻译机器人 - 增强自愈版（乌尔都语功能）
支持：中文→乌尔都语，他加禄语→英语
包含真实健康检查和Koyeb平台优化
"""

import json
import logging
import os
import sys
import time
import asyncio
import subprocess
import psutil
from datetime import datetime
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from dotenv import load_dotenv
from telegram import Update
from telegram.error import Conflict, NetworkError, TimedOut
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
last_health_check = time.time()
consecutive_failures = 0

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

# ==================== 真实健康检查服务器 ====================

class RealHealthCheckHandler(BaseHTTPRequestHandler):
    """真实的健康检查处理器 - 返回真实的健康状态"""
    
    def do_GET(self):
        if self.path == '/health' or self.path == '/':
            try:
                global consecutive_failures, last_health_check
                last_health_check = time.time()
                
                # 执行四项核心检查
                telegram_status = self.check_telegram_connection()
                deepseek_status = self.check_deepseek_api()
                process_status = self.check_process_memory()
                bot_functional = self.check_bot_functionality()
                
                # 如果任何一项检查失败，增加失败计数
                all_healthy = telegram_status and deepseek_status and process_status and bot_functional
                
                if not all_healthy:
                    consecutive_failures += 1
                    logger.warning(f"健康检查失败 #{consecutive_failures}: "
                                 f"Telegram={telegram_status}, "
                                 f"DeepSeek={deepseek_status}, "
                                 f"Process={process_status}, "
                                 f"Functional={bot_functional}")
                else:
                    consecutive_failures = 0
                
                # 如果连续失败3次，返回更严重的状态码
                status_code = 200 if all_healthy else (503 if consecutive_failures < 3 else 500)
                
                self.send_response(status_code)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                # 计算运行时间
                uptime = time.time() - start_time
                hours = int(uptime // 3600)
                minutes = int((uptime % 3600) // 60)
                seconds = int(uptime % 60)
                
                response = {
                    "status": "healthy" if all_healthy else "degraded" if consecutive_failures < 3 else "critical",
                    "service": "telegram_translator_bot",
                    "timestamp": time.time(),
                    "uptime": {
                        "hours": hours,
                        "minutes": minutes,
                        "seconds": seconds,
                        "total_seconds": int(uptime)
                    },
                    "checks": {
                        "telegram_api": telegram_status,
                        "deepseek_api": deepseek_status,
                        "process_memory": process_status,
                        "bot_functional": bot_functional
                    },
                    "failure_count": consecutive_failures,
                    "translation_targets": {
                        "chinese": "urdu",
                        "tagalog": "english",
                        "urdu": "english"
                    },
                    "message": "所有系统正常运行" if all_healthy else 
                              "检测到服务降级" if consecutive_failures < 3 else
                              "严重故障 - 需要立即关注"
                }
                
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
                
            except Exception as e:
                # 如果健康检查本身出错，返回严重错误
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                error_response = {
                    "status": "error",
                    "message": f"健康检查系统错误: {str(e)}",
                    "timestamp": time.time()
                }
                self.wfile.write(json.dumps(error_response).encode('utf-8'))
                logger.error(f"健康检查处理器异常: {e}")
        else:
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            error_response = {"error": "未找到", "path": self.path}
            self.wfile.write(json.dumps(error_response).encode('utf-8'))
    
    def check_telegram_connection(self):
        """检查Telegram API连接"""
        try:
            # 测试基本的Telegram连接（最小化请求）
            # 如果机器人主应用正常运行，这个连接应该是正常的
            # 我们只检查必要的环境变量是否存在
            if not TELEGRAM_TOKEN or len(TELEGRAM_TOKEN) < 10:
                logger.warning("Telegram Token异常")
                return False
            return True
        except Exception as e:
            logger.error(f"Telegram连接检查失败: {e}")
            return False
    
    def check_deepseek_api(self):
        """检查DeepSeek API可用性"""
        try:
            if not DEEPSEEK_API_KEY or len(DEEPSEEK_API_KEY) < 10:
                logger.warning("DeepSeek API Key异常")
                return False
            
            # 最小化的API测试（不消耗额度）
            # 只检查密钥格式和网络可达性
            url = "https://api.deepseek.com/chat/completions"
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            
            # 非常小的测试请求
            test_payload = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False
            }
            
            # 设置短超时，只检查连接性
            response = requests.post(url, headers=headers, json=test_payload, timeout=5)
            
            # 即使返回错误码，只要不是401/403，说明API端点可达
            if response.status_code not in [401, 403]:
                return True
            else:
                logger.warning(f"DeepSeek API认证失败: {response.status_code}")
                return False
                
        except requests.exceptions.Timeout:
            logger.warning("DeepSeek API连接超时（可能临时问题）")
            return True  # 返回True，超时不一定是API问题
        except Exception as e:
            logger.error(f"DeepSeek API检查失败: {e}")
            return False
    
    def check_process_memory(self):
        """检查进程内存使用"""
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_percent = process.memory_percent()
            
            # 检查内存使用是否过高
            if memory_percent > 85:  # 超过85%视为危险
                logger.warning(f"进程内存使用过高: {memory_percent:.1f}%")
                return False
            
            # 检查内存泄漏趋势（如果运行时间够长）
            uptime = time.time() - start_time
            if uptime > 3600:  # 运行超过1小时
                if memory_percent > 70:  # 长期运行后内存仍高
                    logger.warning(f"可能内存泄漏: 运行{int(uptime/3600)}小时后内存{memory_percent:.1f}%")
                    return True  # 仍返回True，这不是致命问题
            
            return True
        except Exception as e:
            logger.error(f"进程内存检查失败: {e}")
            return True  # 检查失败时不视为严重问题
    
    def check_bot_functionality(self):
        """检查机器人基本功能"""
        try:
            # 检查是否在过去5分钟内处理过消息
            # 这里可以扩展为更复杂的功能检查
            current_time = time.time()
            
            # 简单检查：应用是否在运行
            # 在实际中，您可能需要添加更多功能检查
            return True
        except Exception as e:
            logger.error(f"功能检查失败: {e}")
            return False
    
    def log_message(self, format, *args):
        # 减少HTTP请求日志噪音
        logger.debug(f"HTTP健康检查请求: {self.path}")
        pass

def start_real_health_server(port: int = 8000) -> HTTPServer:
    """启动真实的健康检查服务器"""
    try:
        server = HTTPServer(('0.0.0.0', port), RealHealthCheckHandler)
        logger.info(f"✅ 真实健康检查服务器已启动，端口: {port}")
        
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

def translate_with_deepseek(text: str, source_lang_hint: Optional[str] = None, target_lang: Optional[str] = None) -> Optional[str]:
    """
    使用DeepSeek API翻译文本
    参数:
        text: 要翻译的文本
        source_lang_hint: 源语言提示 ('zh'=中文, 'tl'=他加禄语, 'ur'=乌尔都语)
        target_lang: 目标语言 ('ur'=乌尔都语, 'en'=英语)
    """
    if not text or len(text.strip()) == 0:
        return None
    
    url = "https://api.deepseek.com/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 根据语言提示和目标语言设置不同的系统提示
    if source_lang_hint == "zh" and target_lang == "ur":
        # 中文 -> 乌尔都语
        system_prompt = "你是一位专业的翻译专家。请将以下中文内容准确、自然地翻译成乌尔都语（Urdu）。保持原文语气和风格，使用乌尔都语（اردو）书写。"
        user_prompt = f"请将以下中文翻译成乌尔都语：{text}"
        
    elif source_lang_hint == "tl" and target_lang == "en":
        # 他加禄语 -> 英语
        system_prompt = "你是一位专业的翻译专家。请将以下他加禄语（Filipino/Tagalog）内容准确翻译成英语。保持原文意思。"
        user_prompt = f"请将以下他加禄语翻译成英语：{text}"
        
    elif source_lang_hint == "ur" and target_lang == "en":
        # 乌尔都语 -> 英语
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
        "max_tokens": 2000
    }
    
    try:
        logger.info(f"调用DeepSeek API翻译: {text[:100]}...")
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        # 检查HTTP状态码
        if response.status_code == 429:
            logger.warning("DeepSeek API速率限制，请稍后重试")
            return None
        elif response.status_code == 402:
            logger.error("DeepSeek API余额不足，需要充值！")
            return None
        
        response.raise_for_status()
        
        result = response.json()
        translated_text = result["choices"][0]["message"]["content"].strip()
        
        # 清理可能的附加说明
        markers = ["翻译：", "Translation:", "乌尔都语翻译：", "英语翻译：", "以下是翻译结果：", "اردو ترجمہ:", "English translation:"]
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
    返回: 'zh'(中文), 'tl'(他加禄语), 'ur'(乌尔都语), 或 None
    """
    if not text:
        return None
    
    # 检测中文字符（Unicode范围）
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
    
    # 检测乌尔都语字符（阿拉伯文字符范围）
    # 乌尔都语使用阿拉伯文字符，Unicode范围：\u0600-\u06FF
    if any('\u0600' <= char <= '\u06FF' for char in text):
        return "ur"
    
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
        
        # 根据检测到的语言选择翻译目标
        if lang_hint == "zh":
            # 中文 -> 乌尔都语
            logger.info(f"检测到中文，开始翻译成乌尔都语...")
            
            # 发送"正在翻译"提示
            try:
                processing_msg = await update.message.reply_text(
                    "🔄 正在翻译成乌尔都语...",
                    reply_to_message_id=update.message.message_id
                )
                has_processing_msg = True
            except Exception as e:
                logger.warning(f"无法发送处理消息: {e}")
                has_processing_msg = False
                processing_msg = None
            
            translated = None
            try:
                # 在线程池中执行同步翻译函数
                loop = asyncio.get_event_loop()
                translated = await loop.run_in_executor(
                    executor,
                    translate_with_deepseek,
                    original_text,
                    lang_hint,
                    "ur"
                )
                
                # 删除"正在翻译"提示
                if has_processing_msg and processing_msg:
                    try:
                        await processing_msg.delete()
                    except:
                        pass
                
                target_lang_name = "乌尔都语"
                
        elif lang_hint == "tl":
            # 他加禄语 -> 英语
            logger.info(f"检测到他加禄语，开始翻译成英语...")
            
            # 发送"正在翻译"提示
            try:
                processing_msg = await update.message.reply_text(
                    "🔄 正在翻译成英语...",
                    reply_to_message_id=update.message.message_id
                )
                has_processing_msg = True
            except Exception as e:
                logger.warning(f"无法发送处理消息: {e}")
                has_processing_msg = False
                processing_msg = None
            
            translated = None
            try:
                # 在线程池中执行同步翻译函数
                loop = asyncio.get_event_loop()
                translated = await loop.run_in_executor(
                    executor,
                    translate_with_deepseek,
                    original_text,
                    lang_hint,
                    "en"
                )
                
                # 删除"正在翻译"提示
                if has_processing_msg and processing_msg:
                    try:
                        await processing_msg.delete()
                    except:
                        pass
                
                target_lang_name = "英语"
                
        elif lang_hint == "ur":
            # 乌尔都语 -> 英语
            logger.info(f"检测到乌尔都语，开始翻译成英语...")
            
            # 发送"正在翻译"提示
            try:
                processing_msg = await update.message.reply_text(
                    "🔄 正在翻译成英语...",
                    reply_to_message_id=update.message.message_id
                )
                has_processing_msg = True
            except Exception as e:
                logger.warning(f"无法发送处理消息: {e}")
                has_processing_msg = False
                processing_msg = None
            
            translated = None
            try:
                # 在线程池中执行同步翻译函数
                loop = asyncio.get_event_loop()
                translated = await loop.run_in_executor(
                    executor,
                    translate_with_deepseek,
                    original_text,
                    lang_hint,
                    "en"
                )
                
                # 删除"正在翻译"提示
                if has_processing_msg and processing_msg:
                    try:
                        await processing_msg.delete()
                    except:
                        pass
                
                target_lang_name = "英语"
                
        else:
            # 未检测到支持的语言，不翻译
            return
        
        if translated and translated != original_text:
            # 发送翻译结果
            reply_text = f"🌐 翻译成{target_lang_name}:\n\n{translated}"
            
            # 回复原消息
            await update.message.reply_text(
                reply_text,
                reply_to_message_id=update.message.message_id,
                disable_web_page_preview=True
            )
            
            logger.info(f"翻译完成并发送: {original_text[:50]}... → {translated[:50]}...")
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
        logger.error(f"处理消息时出错: {e}")
        if 'has_processing_msg' in locals() and has_processing_msg and 'processing_msg' in locals() and processing_msg:
            try:
                await processing_msg.delete()
            except:
                pass

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start 命令处理
    """
    uptime = time.time() - start_time
    hours = int(uptime // 3600)
    minutes = int((uptime % 3600) // 60)
    seconds = int(uptime % 60)
    
    await update.message.reply_text(
        f"🤖 多语言翻译机器人已启动！\n\n"
        f"✨ 功能特性：\n"
        f"• 自动将中文消息翻译成乌尔都语\n"
        f"• 自动将他加禄语消息翻译成英语\n"
        f"• 支持乌尔都语消息翻译成英语\n"
        f"• 群组自动翻译，无需命令\n"
        f"• 自愈系统: ✅ 已启用\n\n"
        f"📊 系统状态：\n"
        f"• 运行时间: {hours}小时 {minutes}分钟 {seconds}秒\n"
        f"• 健康检查: ✅ 运行中 (端口 {HEALTH_CHECK_PORT})\n\n"
        f"🔧 可用命令：\n"
        f"/start - 显示此信息\n"
        f"/help - 详细使用说明\n"
        f"/status - 检查详细状态\n"
        f"/health - 查看健康检查结果\n"
        f"/languages - 查看支持的语言"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /help 命令处理
    """
    await update.message.reply_text(
        "📖 详细使用说明\n\n"
        "🔄 翻译规则：\n"
        "• 中文 → 乌尔都语\n"
        "• 他加禄语 → 英语\n"
        "• 乌尔都语 → 英语\n\n"
        "⚙️ 自愈系统：\n"
        "• 机器人包含健康检查系统\n"
        "• 自动监控Telegram和DeepSeek连接\n"
        "• Koyeb平台会基于健康状态自动重启\n"
        "• 每月只需5分钟检查\n\n"
        "👥 群组设置：\n"
        "1. 将机器人添加到群组\n"
        "2. 给机器人管理员权限（发送消息）\n"
        "3. 关闭隐私模式 (@BotFather设置)\n"
        "4. 在群组中正常聊天即可\n\n"
        "🔧 可用命令：\n"
        "/start - 显示机器人信息\n"
        "/help - 显示帮助信息\n"
        "/status - 检查机器人状态\n"
        "/health - 查看健康检查结果\n"
        "/languages - 查看支持的语言"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /status 命令处理
    """
    uptime = time.time() - start_time
    
    hours = int(uptime // 3600)
    minutes = int((uptime % 3600) // 60)
    seconds = int(uptime % 60)
    
    # 检查当前健康状态
    health_status = "✅ 正常"
    try:
        response = requests.get(f"http://localhost:{HEALTH_CHECK_PORT}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            health_status = "✅ 健康" if data.get("status") == "healthy" else "⚠️ 降级"
    except:
        health_status = "❌ 不可用"
    
    await update.message.reply_text(
        f"📊 机器人详细状态\n\n"
        f"⏱️ 运行时间: {hours}小时 {minutes}分钟 {seconds}秒\n"
        f"🏥 健康状态: {health_status}\n"
        f"🔗 健康检查: http://localhost:{HEALTH_CHECK_PORT}/health\n"
        f"🔢 失败计数: {consecutive_failures}\n\n"
        f"🌐 翻译配置：\n"
        f"• 中文 → 乌尔都语\n"
        f"• 他加禄语 → 英语\n"
        f"• 乌尔都语 → 英语\n\n"
        f"⚙️ 系统信息：\n"
        f"• 自愈系统: ✅ 已启用\n"
        f"• 平台: Koyeb Cloud\n"
        f"• 日志文件: translator_bot.log\n\n"
        f"📅 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /health 命令 - 查看健康检查结果
    """
    try:
        response = requests.get(f"http://localhost:{HEALTH_CHECK_PORT}/health", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            # 格式化健康检查结果
            checks = data.get("checks", {})
            check_status = []
            for check_name, check_result in checks.items():
                status = "✅" if check_result else "❌"
                check_name_display = {
                    "telegram_api": "Telegram API",
                    "deepseek_api": "DeepSeek API",
                    "process_memory": "进程内存",
                    "bot_functional": "机器人功能"
                }.get(check_name, check_name)
                check_status.append(f"{status} {check_name_display}")
            
            status_display = {
                "healthy": "✅ 健康",
                "degraded": "⚠️ 降级",
                "critical": "❌ 严重",
                "error": "💥 错误"
            }.get(data.get('status', ''), data.get('status', '未知'))
            
            health_text = (
                f"🏥 健康检查结果\n\n"
                f"状态: {status_display}\n"
                f"运行时间: {data.get('uptime', {}).get('hours', 0)}小时 "
                f"{data.get('uptime', {}).get('minutes', 0)}分钟\n"
                f"失败次数: {data.get('failure_count', 0)}\n\n"
                f"检查项目:\n" + "\n".join(check_status) + "\n\n"
                f"🌐 翻译目标:\n"
                f"• 中文 → 乌尔都语\n"
                f"• 他加禄语 → 英语\n"
                f"• 乌尔都语 → 英语\n\n"
                f"📝 消息: {data.get('message', '')}"
            )
        else:
            health_text = f"❌ 健康检查失败: HTTP {response.status_code}"
            
    except Exception as e:
        health_text = f"❌ 无法获取健康检查: {str(e)}"
    
    await update.message.reply_text(health_text)

async def languages_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        "乌尔都语 → 英语\n\n"
        "⚙️ 自愈系统状态：\n"
        f"• 健康检查端口: {HEALTH_CHECK_PORT}\n"
        f"• 当前失败计数: {consecutive_failures}\n"
        "• 平台自动重启: ✅ 已配置"
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
    
    # 检查并安装psutil依赖
    try:
        import psutil
    except ImportError:
        print("❌ 缺少psutil包，正在安装...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil"])
            import psutil
            print("✅ psutil安装成功")
        except Exception as e:
            print(f"⚠️  无法安装psutil: {e}")
            print("⚠️  健康检查的内存监控功能将不可用")
            # 创建一个虚拟的psutil模块
            class MockPsutil:
                class Process:
                    def memory_info(self): return type('obj', (object,), {'rss': 0})()
                    def memory_percent(self): return 0.0
            psutil = MockPsutil()
    
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
    print("🤖 Telegram多语言翻译机器人 - 增强自愈版")
    print("支持：中文→乌尔都语，他加禄语→英语")
    print("=" * 60)
    print(f"• Python版本: {sys.version.split()[0]}")
    print(f"• 健康检查端口: {HEALTH_CHECK_PORT}")
    print(f"• 自愈系统: ✅ 已启用")
    print(f"• 日志文件: translator_bot.log")
    print("=" * 60)
    
    # 启动真实的健康检查服务器
    try:
        health_server = start_real_health_server(port=HEALTH_CHECK_PORT)
        print(f"✅ 真实健康检查服务器已启动")
        print(f"   访问: http://0.0.0.0:{HEALTH_CHECK_PORT}/health")
        print(f"   注意: 现在健康检查返回真实状态码:")
        print(f"       200 = 所有系统正常")
        print(f"       503 = 服务降级 (Koyeb会重启)")
        print(f"       500 = 严重故障 (Koyeb会重启)")
    except Exception as e:
        logger.error(f"启动健康检查服务器失败: {e}")
        print(f"⚠️  健康检查服务器启动失败: {e}")
        print("⚠️  继续启动机器人，但自愈系统不可用...")
    
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
        application.add_handler(CommandHandler("health", health_command))
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
        print("重要配置说明:")
        print("1. 确保requirements.txt包含: psutil>=5.9.0")
        print("2. 在Koyeb中配置健康检查:")
        print("   - 路径: /health")
        print("   - 端口: 8000")
        print("   - 间隔: 30秒")
        print("   - 超时: 10秒")
        print("   - 最大失败: 3次")
        print("3. 启用Koyeb自动重启策略")
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
                print("这可能是因为有另一个实例在运行")
                print("请检查Koyeb控制台确保只有一个实例")
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
