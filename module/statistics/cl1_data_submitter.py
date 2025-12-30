# -*- coding: utf-8 -*-
"""
CL1 数据自动提交模块
负责收集侵蚀1统计数据并定时提交到云端API
"""
from __future__ import annotations

import hashlib
import json
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

import requests

from module.logger import logger


def generate_device_id() -> str:
    """
    基于设备信息生成唯一标识符
    使用多个硬件/系统信息的组合来确保唯一性
    
    Returns:
        str: 32位十六进制字符串作为设备唯一标识
    """
    try:
        # 收集设备信息
        info_parts = [
            platform.node(),  # 计算机名
            platform.machine(),  # 机器类型
            platform.processor(),  # 处理器信息
            platform.system(),  # 操作系统
        ]
        
        # 尝试获取MAC地址
        try:
            import uuid
            mac = uuid.getnode()
            info_parts.append(str(mac))
        except Exception:
            pass
        
        # 尝试获取磁盘序列号 (Windows)
        if platform.system() == 'Windows':
            try:
                import subprocess
                result = subprocess.check_output(
                    'wmic diskdrive get serialnumber',
                    shell=True,
                    stderr=subprocess.DEVNULL
                ).decode('utf-8', errors='ignore')
                serial = result.split('\n')[1].strip()
                if serial:
                    info_parts.append(serial)
            except Exception:
                pass
        
        # 组合所有信息并生成哈希
        combined = '|'.join(filter(None, info_parts))
        device_hash = hashlib.md5(combined.encode('utf-8')).hexdigest()
        
        return device_hash
    except Exception as e:
        logger.warning(f'Failed to generate device ID: {e}')
        # 降级方案: 使用随机UUID
        import uuid
        return uuid.uuid4().hex


class Cl1DataSubmitter:
    """CL1数据提交器"""
    
    def __init__(self, endpoint: str = 'https://alascloudapi.nanoda.work/api/telemetry'):
        """
        初始化数据提交器
        
        Args:
            endpoint: API端点URL
        """
        self.endpoint = endpoint
        self._device_id: Optional[str] = None
        self._last_submit_time: float = 0
        self._submit_interval: int = 3600  # 1小时
        
        # 获取项目根目录
        self.project_root = Path(__file__).resolve().parents[2]
        self.cl1_dir = self.project_root / 'log' / 'cl1'
        self.cl1_file = self.cl1_dir / 'cl1_monthly.json'
    
    @property
    def device_id(self) -> str:
        """获取设备ID (懒加载)"""
        if self._device_id is None:
            self._device_id = self._load_or_generate_device_id()
        return self._device_id
    
    def _load_or_generate_device_id(self) -> str:
        """
        从文件加载或生成新的设备ID
        设备ID会被保存到 cl1_monthly.json 中以保持一致性
        """
        try:
            if self.cl1_file.exists():
                with self.cl1_file.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict) and 'device_id' in data:
                        logger.info(f'Loaded existing device ID')
                        return data['device_id']
        except Exception as e:
            logger.warning(f'Failed to load device ID from file: {e}')
        
        # 生成新的设备ID
        device_id = generate_device_id()
        logger.info(f'Generated new device ID: {device_id[:8]}...')
        
        # 保存到文件
        try:
            self._save_device_id(device_id)
        except Exception as e:
            logger.warning(f'Failed to save device ID: {e}')
        
        return device_id
    
    def _save_device_id(self, device_id: str):
        """保存设备ID到cl1_monthly.json"""
        try:
            self.cl1_dir.mkdir(parents=True, exist_ok=True)
            
            # 读取现有数据
            data = {}
            if self.cl1_file.exists():
                try:
                    with self.cl1_file.open('r', encoding='utf-8') as f:
                        data = json.load(f)
                        if not isinstance(data, dict):
                            data = {}
                except Exception:
                    pass
            
            # 添加设备ID
            data['device_id'] = device_id
            
            # 写回文件
            with self.cl1_file.open('w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception(f'Failed to save device ID to file: {e}')
    
    def collect_data(self, year: int = None, month: int = None) -> Dict[str, Any]:
        """
        收集指定月份的CL1统计数据
        
        Args:
            year: 年份 (默认当前年份)
            month: 月份 (默认当前月份)
        
        Returns:
            包含统计数据的字典
        """
        now = datetime.now()
        if year is None:
            year = now.year
        if month is None:
            month = now.month
        
        month_key = f"{year:04d}-{month:02d}"
        
        # 读取cl1_monthly.json
        try:
            if not self.cl1_file.exists():
                logger.warning('CL1 monthly file does not exist')
                return self._empty_data(month_key)
            
            with self.cl1_file.open('r', encoding='utf-8') as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    logger.warning('CL1 monthly file is not a dict')
                    return self._empty_data(month_key)
        except Exception as e:
            logger.exception(f'Failed to load CL1 monthly file: {e}')
            return self._empty_data(month_key)
        
        # 提取数据
        battle_count = int(data.get(month_key, 0))
        akashi_encounters = int(data.get(f"{month_key}-akashi", 0))
        akashi_ap = int(data.get(f"{month_key}-akashi-ap", 0))
        
        # 如果没有明确的akashi-ap字段,尝试从entries计算
        if akashi_ap == 0:
            entries = data.get(f"{month_key}-akashi-ap-entries", [])
            if isinstance(entries, list):
                for entry in entries:
                    try:
                        if isinstance(entry, dict):
                            akashi_ap += int(entry.get('amount', 0))
                        else:
                            akashi_ap += int(entry)
                    except Exception:
                        continue
        
        return {
            'month': month_key,
            'battle_count': battle_count,
            'akashi_encounters': akashi_encounters,
            'akashi_ap': akashi_ap,
        }
    
    def _empty_data(self, month_key: str) -> Dict[str, Any]:
        """返回空数据"""
        return {
            'month': month_key,
            'battle_count': 0,
            'akashi_encounters': 0,
            'akashi_ap': 0,
        }
    
    def calculate_metrics(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        计算各项指标
        
        Args:
            raw_data: 原始统计数据
        
        Returns:
            包含计算后指标的完整数据
        """
        battle_count = raw_data['battle_count']
        akashi_encounters = raw_data['akashi_encounters']
        akashi_ap = raw_data['akashi_ap']
        
        # 计算战斗轮次 (每2次战斗为1轮)
        battle_rounds = battle_count // 2
        
        # 计算出击消耗 (每轮消耗120行动力)
        sortie_cost = battle_rounds * 120
        
        # 计算明石遇见概率
        if battle_count > 0:
            akashi_probability = round(akashi_encounters / battle_count, 4)
        else:
            akashi_probability = 0.0
        
        # 计算平均体力 (每次从明石处获得的平均行动力)
        if akashi_encounters > 0:
            average_stamina = round(akashi_ap / akashi_encounters, 2)
        else:
            average_stamina = 0.0
        
        # 净赚体力 = 从明石获得的总行动力
        net_stamina_gain = akashi_ap
        
        return {
            'device_id': self.device_id,
            'month': raw_data['month'],
            'battle_count': battle_count,
            'battle_rounds': battle_rounds,
            'sortie_cost': sortie_cost,
            'akashi_encounters': akashi_encounters,
            'akashi_probability': akashi_probability,
            'average_stamina': average_stamina,
            'net_stamina_gain': net_stamina_gain,
        }
    
    def submit_data(self, data: Dict[str, Any], timeout: int = 10) -> bool:
        """
        提交数据到API
        
        Args:
            data: 要提交的数据
            timeout: 请求超时时间(秒)
        
        Returns:
            是否提交成功
        """
        try:
            # 如果没有任何战斗数据,不提交
            if data.get('battle_count', 0) == 0:
                logger.info('No CL1 battle data to submit')
                return False
            
            logger.info(f'Submitting CL1 data for {data["month"]}...')
            logger.attr('battle_count', data['battle_count'])
            logger.attr('akashi_encounters', data['akashi_encounters'])
            logger.attr('akashi_probability', f"{data['akashi_probability']:.2%}")
            
            response = requests.post(
                self.endpoint,
                json=data,
                timeout=timeout,
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code == 200:
                logger.info('✓ CL1 data submitted successfully')
                return True
            else:
                logger.warning(f'✗ CL1 data submission failed: HTTP {response.status_code}')
                logger.warning(f'Response: {response.text[:200]}')
                return False
        
        except requests.exceptions.Timeout:
            logger.warning(f'CL1 data submission timeout after {timeout}s')
            return False
        except requests.exceptions.RequestException as e:
            logger.warning(f'CL1 data submission failed: {e}')
            return False
        except Exception as e:
            logger.exception(f'Unexpected error during CL1 data submission: {e}')
            return False
    
    def should_submit(self) -> bool:
        """
        检查是否应该提交数据
        基于时间间隔判断
        
        Returns:
            是否应该提交
        """
        current_time = time.time()
        if current_time - self._last_submit_time >= self._submit_interval:
            return True
        return False
    
    def auto_submit(self) -> bool:
        """
        自动提交当月数据
        会检查时间间隔,避免频繁提交
        
        Returns:
            是否成功提交
        """
        if not self.should_submit():
            return False
        
        try:
            # 收集数据
            raw_data = self.collect_data()
            
            # 计算指标
            metrics = self.calculate_metrics(raw_data)
            
            # 提交数据
            success = self.submit_data(metrics)
            
            if success:
                self._last_submit_time = time.time()
            
            return success
        
        except Exception as e:
            logger.exception(f'Failed to auto submit CL1 data: {e}')
            return False
    
    def auto_submit_daemon(self):
        """
        定时提交守护进程 (生成器函数,用于task_handler)
        每次被调用时检查是否需要提交
        """
        while True:
            try:
                self.auto_submit()
            except Exception as e:
                logger.exception(f'Error in CL1 auto submit daemon: {e}')
            yield


# 全局单例
_submitter: Optional[Cl1DataSubmitter] = None


def get_cl1_submitter() -> Cl1DataSubmitter:
    """获取CL1数据提交器单例"""
    global _submitter
    if _submitter is None:
        _submitter = Cl1DataSubmitter()
    return _submitter


__all__ = ['Cl1DataSubmitter', 'get_cl1_submitter', 'generate_device_id']
