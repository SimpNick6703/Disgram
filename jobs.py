"""
Job Management System for Disgram
Handles storage, retrieval, and persistence of forwarding jobs
"""

import json
import os
import threading
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
import datetime


@dataclass
class Job:
    """Represents a Telegram-to-Discord forwarding job"""
    jobName: str
    webhookUrl: str
    telegramChannels: List[str]
    threadId: Optional[str] = None
    embedColor: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert job to dictionary"""
        return {
            'jobName': self.jobName,
            'webhookUrl': self.webhookUrl,
            'telegramChannels': self.telegramChannels,
            'threadId': self.threadId,
            'embedColor': self.embedColor
        }
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'Job':
        """Create Job from dictionary"""
        return Job(
            jobName=data['jobName'],
            webhookUrl=data['webhookUrl'],
            telegramChannels=data['telegramChannels'],
            threadId=data.get('threadId'),
            embedColor=data.get('embedColor')
        )


class JobManager:
    """Manages all forwarding jobs with thread-safe operations"""
    
    def __init__(self, storage_file: str = "jobs.json"):
        self.storage_file = storage_file
        self.jobs: Dict[str, Job] = {}
        self.lock = threading.RLock()
        self._load_jobs()
    
    def _load_jobs(self):
        """Load jobs from storage file"""
        with self.lock:
            if os.path.exists(self.storage_file):
                try:
                    with open(self.storage_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        self.jobs = {
                            name: Job.from_dict(job_data)
                            for name, job_data in data.items()
                        }
                    self._log_system(f"Loaded {len(self.jobs)} job(s) from storage")
                except Exception as e:
                    self._log_system(f"Error loading jobs: {e}", log_type="error")
                    self.jobs = {}
            else:
                self.jobs = {}
                self._log_system("No existing jobs file found, starting fresh")
    
    def _save_jobs(self):
        """Persist jobs to storage file"""
        with self.lock:
            try:
                data = {name: job.to_dict() for name, job in self.jobs.items()}
                with open(self.storage_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                self._log_system(f"Saved {len(self.jobs)} job(s) to storage")
            except Exception as e:
                self._log_system(f"Error saving jobs: {e}", log_type="error")
    
    def _log_system(self, message: str, log_type: str = "info"):
        """Log system-level job management events"""
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"{timestamp} [SYSTEM] {message}"
        print(log_entry)
        if log_type in ["error", "system"]:
            with open("Disgram.log", "a", encoding='utf-8') as log_file:
                log_file.write(log_entry + "\n")
    
    def add_job(self, job: Job) -> tuple[bool, str]:
        """
        Add a new job
        Returns: (success: bool, message: str)
        """
        with self.lock:
            if job.jobName in self.jobs:
                return False, f"Job '{job.jobName}' already exists"
            
            # Validate job
            if not job.webhookUrl:
                return False, "Webhook URL is required"
            if not job.telegramChannels or len(job.telegramChannels) == 0:
                return False, "At least one Telegram channel is required"
            
            self.jobs[job.jobName] = job
            self._save_jobs()
            self._log_system(f"Created job '{job.jobName}' with {len(job.telegramChannels)} channel(s)", log_type="system")
            return True, f"Job '{job.jobName}' created successfully"
    
    def update_job(self, job_name: str, updates: Dict[str, Any]) -> tuple[bool, str]:
        """
        Update an existing job
        Returns: (success: bool, message: str)
        """
        with self.lock:
            if job_name not in self.jobs:
                return False, f"Job '{job_name}' not found"
            
            job = self.jobs[job_name]
            
            # Update fields if provided
            if 'webhookUrl' in updates and updates['webhookUrl']:
                job.webhookUrl = updates['webhookUrl']
            if 'telegramChannels' in updates and updates['telegramChannels']:
                job.telegramChannels = updates['telegramChannels']
            if 'threadId' in updates:
                job.threadId = updates['threadId']
            if 'embedColor' in updates:
                job.embedColor = updates['embedColor']
            
            self._save_jobs()
            self._log_system(f"Updated job '{job_name}'", log_type="system")
            return True, f"Job '{job_name}' updated successfully"
    
    def delete_job(self, job_name: str) -> tuple[bool, str]:
        """
        Delete a job
        Returns: (success: bool, message: str)
        """
        with self.lock:
            if job_name not in self.jobs:
                return False, f"Job '{job_name}' not found"
            
            del self.jobs[job_name]
            self._save_jobs()
            self._log_system(f"Deleted job '{job_name}'", log_type="system")
            return True, f"Job '{job_name}' deleted successfully"
    
    def get_job(self, job_name: str) -> Optional[Job]:
        """Get a job by name"""
        with self.lock:
            return self.jobs.get(job_name)
    
    def get_all_jobs(self) -> Dict[str, Job]:
        """Get all jobs"""
        with self.lock:
            return self.jobs.copy()
    
    def job_exists(self, job_name: str) -> bool:
        """Check if a job exists"""
        with self.lock:
            return job_name in self.jobs


# Global job manager instance
job_manager = JobManager()
