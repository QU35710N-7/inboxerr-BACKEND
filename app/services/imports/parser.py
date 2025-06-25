"""
Enhanced Streaming CSV parser service for import pipeline.

This module provides memory-efficient CSV parsing with intelligent column detection,
real-time progress tracking, error handling, and bulk database operations for contact imports.

PRODUCTION ENHANCEMENTS:
- Intelligent multi-column phone detection with confidence scoring
- Advanced name detection using linguistic patterns
- User feedback for low-confidence detections
- Enhanced error handling and recovery suggestions
- Optimized progress events with new ImportProgressV1 schema
"""
import csv
import hashlib
import logging
import asyncio
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, AsyncGenerator, Tuple
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import select, func

from app.models.import_job import ImportJob, ImportStatus
from app.models.contact import Contact
from app.schemas.contact import ContactCreate
from app.schemas.import_job import ImportError
from app.core.exceptions import ValidationError
from app.utils.phone import validate_phone
from app.db.session import get_repository_context
from app.utils.ids import generate_prefixed_id, IDPrefix
from app.services.imports.events import (
    ImportProgressV1, ImportEventType, ImportErrorV1, 
    create_progress_event, create_completion_event, create_failure_event
)

logger = logging.getLogger("inboxerr.parser")

# Enhanced regex patterns for production
_PHONE_HEADER_RGX = re.compile(
    r"(phone[\s\d\w]*|mobile[\s\d\w]*|cell[\s\d\w]*|tel[\s\d\w]*|"
    r"contact[\s\d\w]*|whatsapp|number[\s\d]*|ph\s*\d*|mob\s*\d*|"
    r"telephone|fone|fon)", 
    re.I
)
_NAME_HEADER_RGX = re.compile(r"(name|contact|person|client|customer|full_name|first_name|last_name)", re.I)
_EMAIL_HEADER_RGX = re.compile(r"(email|mail|e-mail)", re.I)


def _enhanced_phone_column_score(header: str, samples: list[str]) -> float:
    """
    Enhanced phone column scoring with better edge case handling.
    
    Scoring breakdown:
    - Header match: 20 points
    - Data validity: 80 points (with proper empty cell handling)
    - Penalty for mostly empty columns
    
    Args:
        header: Column header name
        samples: Sample values from the column
        
    Returns:
        float: Score from 0-100 indicating how likely this is a phone column
    """
    # Header bonus for phone-related terms
    header_bonus = 20 if _PHONE_HEADER_RGX.search(header) else 0
    
    if not samples:
        return header_bonus * 0.1  # Heavy penalty for completely empty columns
    
    valid_phones = 0
    total_non_empty = 0
    suspicious_patterns = 0
    
    for raw in samples:
        raw = raw.strip()
        if not raw:
            continue  # Skip empty cells entirely
            
        total_non_empty += 1
        
        # Quick rejection for obviously non-phone data
        if len(raw) > 25:  # Too long to be a phone number
            suspicious_patterns += 1
            continue
            
        if len(raw) < 5:  # Too short to be a valid phone
            suspicious_patterns += 1
            continue
            
        # Check for non-phone patterns (emails, URLs, etc.)
        if '@' in raw or 'http' in raw.lower() or raw.isalpha():
            suspicious_patterns += 1
            continue
            
        # Use production phone validation
        is_valid, *_ = validate_phone(raw)
        if is_valid:
            valid_phones += 1
    
    # Avoid division by zero and handle edge cases
    if total_non_empty == 0:
        return header_bonus * 0.1  # Heavily penalize empty columns
    
    # Calculate data score with penalties
    validity_ratio = valid_phones / total_non_empty
    suspicious_ratio = suspicious_patterns / total_non_empty
    
    # Penalize columns with too many suspicious patterns
    if suspicious_ratio > 0.5:
        validity_ratio *= 0.3  # Heavy penalty for mostly non-phone data
    
    data_score = validity_ratio * 80
    
    return header_bonus + data_score


def _enhanced_name_column_score(header: str, samples: list[str]) -> float:
    """
    Enhanced name column detection using linguistic patterns.
    
    Scoring factors:
    - Header match: 30 points
    - Average length (3-50 chars): 20 points  
    - Contains spaces (first/last names): 15 points
    - Mostly alphabetic: 25 points
    - Reasonable name patterns: 10 points
    
    Args:
        header: Column header name
        samples: Sample values from the column
        
    Returns:
        float: Score from 0-100 indicating how likely this is a name column
    """
    score = 0
    
    # Header bonus for name-related terms
    if _NAME_HEADER_RGX.search(header):
        score += 30
    
    if not samples:
        return score * 0.1
    
    # Filter out empty values
    non_empty_values = [v.strip() for v in samples if v and v.strip()]
    
    if not non_empty_values:
        return score * 0.1
    
    # Analyze name-like characteristics
    total_values = len(non_empty_values)
    
    # 1. Average length check (reasonable name length)
    avg_length = sum(len(v) for v in non_empty_values) / total_values
    if 3 <= avg_length <= 50:
        score += 20
    elif avg_length > 50:  # Probably not names if too long
        score -= 10
    
    # 2. Space analysis (first/last name patterns)
    has_spaces = sum(1 for v in non_empty_values if ' ' in v) / total_values
    if has_spaces > 0.3:  # 30%+ have spaces
        score += 15
    elif has_spaces > 0.1:  # Some spaces
        score += 8
    
    # 3. Alphabetic content analysis
    mostly_alpha = sum(1 for v in non_empty_values 
                      if re.match(r"^[a-zA-Z\s\'-\.]+$", v)) / total_values
    if mostly_alpha > 0.7:  # 70%+ alphabetic
        score += 25
    elif mostly_alpha > 0.5:  # 50%+ alphabetic
        score += 15
    
    # 4. Common name patterns
    common_name_patterns = 0
    for value in non_empty_values[:100]:  # Sample first 100 for performance
        # Title case pattern (John Smith)
        if value.istitle():
            common_name_patterns += 1
        # All caps might be names too (JOHN SMITH)
        elif value.isupper() and len(value.split()) <= 3:
            common_name_patterns += 1
    
    if common_name_patterns > 0:
        pattern_ratio = common_name_patterns / min(len(non_empty_values), 100)
        if pattern_ratio > 0.3:
            score += 10
        elif pattern_ratio > 0.1:
            score += 5
    
    # Penalty for obvious non-name patterns
    non_name_patterns = sum(1 for v in non_empty_values[:50] 
                           if '@' in v or v.isdigit() or len(v) < 2)
    if non_name_patterns > 0:
        penalty_ratio = non_name_patterns / min(len(non_empty_values), 50)
        score -= penalty_ratio * 30
    
    return max(0, score)  # Don't return negative scores


class CSVParserConfig:
    """Enhanced configuration for CSV parser behavior."""
    
    # Memory and performance limits
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
    MAX_ROWS = 1_000_000  # 1M rows max
    BULK_INSERT_SIZE = 1000  # Process in chunks of 1000 rows
    
    # CSV parsing settings
    ALLOWED_DELIMITERS = [',', '\t', '|', ';']
    ENCODING_FALLBACKS = ['utf-8', 'latin1', 'cp1252']
    
    # Column detection settings
    COLUMN_SAMPLE_SIZE = 1000  # Rows to sample for column detection
    MIN_PHONE_CONFIDENCE = 30  # Minimum confidence for phone column
    MIN_NAME_CONFIDENCE = 20   # Minimum confidence for name column
    MAX_PHONE_CANDIDATES = 3   # Maximum phone columns to consider
    
    # Error limits
    MAX_ERRORS_PER_JOB = 10000  # Stop processing if too many errors
    ERROR_SAMPLE_SIZE = 100     # Only store first 100 errors
    
    # Progress reporting
    PROGRESS_UPDATE_INTERVAL = 0.5  # Seconds between progress updates


class ColumnDetectionResult:
    """Enhanced result object for column detection with confidence metrics."""
    
    def __init__(self):
        self.primary_phone_column: Optional[str] = None
        self.phone_candidates: List[Dict[str, Any]] = []
        self.name_column: Optional[str] = None
        self.detected_columns: Dict[str, Any] = {}
        self.confidence_scores: Dict[str, float] = {}
        self.detection_quality: str = "unknown"  # high, medium, low
        self.user_guidance: List[str] = []
        
    @property
    def phone_confidence(self) -> float:
        """Get confidence score for primary phone column."""
        return self.confidence_scores.get("phone", 0.0)
    
    @property
    def name_confidence(self) -> float:
        """Get confidence score for name column."""
        return self.confidence_scores.get("name", 0.0)
    
    @property
    def needs_manual_review(self) -> bool:
        """Check if detection confidence is too low for automatic processing."""
        return self.phone_confidence < CSVParserConfig.MIN_PHONE_CONFIDENCE


class CSVParseResult:
    """Enhanced result object for CSV parsing operations."""
    
    def __init__(self):
        self.total_rows = 0
        self.processed_rows = 0
        self.successful_contacts = 0
        self.errors: List[ImportError] = []
        self.status = ImportStatus.PROCESSING
        self.sha256_hash = ""
        self.column_detection: ColumnDetectionResult = ColumnDetectionResult()
        
        # Performance tracking
        self.start_time: Optional[datetime] = None
        self.processing_rate = 0  # rows per second
        self.memory_usage_mb = 0.0
        
    @property
    def progress_percentage(self) -> float:
        """Calculate progress percentage with bounds checking."""
        if self.total_rows == 0:
            return 0.0
        return round(min(100.0, max(0.0, (self.processed_rows / self.total_rows) * 100)), 2)
    
    @property
    def error_count(self) -> int:
        """Get total error count."""
        return len(self.errors)
    
    @property
    def has_critical_errors(self) -> bool:
        """Check if there are too many errors to continue."""
        return self.error_count >= CSVParserConfig.MAX_ERRORS_PER_JOB
    
    @property
    def estimated_completion_time(self) -> str:
        """Calculate estimated completion time based on current processing rate."""
        if self.processing_rate <= 0 or self.processed_rows >= self.total_rows:
            return "Calculating..."
        
        remaining_rows = self.total_rows - self.processed_rows
        estimated_seconds = remaining_rows / self.processing_rate
        
        if estimated_seconds < 60:
            return f"~{int(estimated_seconds)} seconds"
        elif estimated_seconds < 3600:
            return f"~{int(estimated_seconds / 60)} minutes"
        else:
            return f"~{int(estimated_seconds / 3600)} hours"


class StreamingCSVParser:
    """
    Enhanced memory-efficient streaming CSV parser for contact imports.
    
    This parser processes large CSV files in chunks while maintaining constant
    memory usage and providing intelligent column detection with confidence scoring.
    """
    
    def __init__(self, session: AsyncSession):
        """
        Initialize the enhanced CSV parser.
        
        Args:
            session: Async database session for operations
        """
        self.session = session
        self.config = CSVParserConfig()
        self._last_progress_update = 0
        
    async def parse_file(
        self,
        file_path: Path,
        import_job_id: str,
        progress_callback: Optional[callable] = None
    ) -> CSVParseResult:
        """
        Parse a CSV file and import contacts to database with enhanced column detection.
        
        Args:
            file_path: Path to the CSV file to parse
            import_job_id: ID of the import job tracking this operation
            progress_callback: Optional callback for progress updates
            
        Returns:
            CSVParseResult: Enhanced results of the parsing operation
            
        Raises:
            ValidationError: If file validation fails
            FileNotFoundError: If file doesn't exist
            PermissionError: If file can't be read
        """
        logger.info(f"Starting enhanced CSV parse for import job {import_job_id}")
        result = CSVParseResult()
        result.start_time = datetime.now(timezone.utc)
        
        try:
            # Phase 1: File validation and setup
            await self._validate_file(file_path)
            result.sha256_hash = await self._calculate_file_hash(file_path)
            
            # Update import job with initial status
            await self._update_import_job(import_job_id, {
                'status': ImportStatus.PROCESSING,
                'sha256': result.sha256_hash,
                'started_at': result.start_time
            })
            
            # Phase 2: File format detection
            encoding, delimiter = await self._detect_file_format(file_path)
            logger.info(f"Detected encoding: {encoding}, delimiter: '{delimiter}'")
            
            # Phase 3: Enhanced column detection
            headers = await self._parse_headers(file_path, encoding, delimiter)
            result.column_detection = await self._enhanced_column_detection(
                file_path, encoding, delimiter, headers
            )
            
            # Check if manual review is needed
            if result.column_detection.needs_manual_review:
                logger.warning(f"Low confidence column detection for job {import_job_id}")
                # You might want to pause here and ask for user input in production
            
            # Phase 4: Row counting and validation
            result.total_rows = await self._count_csv_rows(file_path, encoding)
            logger.info(f"Total rows to process: {result.total_rows}")
            
            if result.total_rows > self.config.MAX_ROWS:
                raise ValidationError(
                    f"File has {result.total_rows:,} rows, maximum allowed is {self.config.MAX_ROWS:,}"
                )
            
            # Update import job with detection results
            await self._update_import_job(import_job_id, {
                'rows_total': result.total_rows,
                'detected_columns': result.column_detection.detected_columns
            })
            
            # Phase 5: Enhanced streaming processing
            async for chunk_result in self._enhanced_process_csv_chunks(
                file_path, encoding, delimiter, import_job_id, result
            ):
                result.processed_rows += chunk_result['processed']
                result.successful_contacts += chunk_result['successful']
                result.errors.extend(chunk_result['errors'])
                result.processing_rate = chunk_result.get('processing_rate', 0)
                result.memory_usage_mb = chunk_result.get('memory_usage_mb', 0.0)
                
                # Enhanced progress reporting with rate limiting
                current_time = datetime.now(timezone.utc).timestamp()
                if (current_time - self._last_progress_update) >= self.config.PROGRESS_UPDATE_INTERVAL:
                    
                    # Update database
                    await self._update_import_job(import_job_id, {
                        'rows_processed': result.processed_rows,
                        'errors': [error.dict() for error in result.errors[:self.config.ERROR_SAMPLE_SIZE]]
                    })
                    
                    # Enhanced progress callback
                    if progress_callback:
                        # Convert ImportError objects to ImportErrorV1 format
                        error_events = [
                            ImportErrorV1(
                                row=error.row,
                                column=error.column,
                                message=error.message,
                                value=error.value
                            ) for error in chunk_result["errors"]
                        ]
                        
                        progress_event = create_progress_event(
                            job_id=import_job_id,
                            processed=result.processed_rows,
                            successful=result.successful_contacts,
                            total_rows=result.total_rows,
                            errors=error_events,
                            error_count=result.error_count,
                            has_critical_errors=result.has_critical_errors,
                            estimated_completion=result.estimated_completion_time,
                            processing_rate=int(result.processing_rate),
                            memory_usage_mb=result.memory_usage_mb
                        )
                        
                        await progress_callback(progress_event)
                    
                    self._last_progress_update = current_time
                
                # Check for critical errors
                if result.has_critical_errors:
                    logger.warning(f"Too many errors ({result.error_count}), stopping import")
                    result.status = ImportStatus.FAILED
                    break
            
            # Phase 6: Final status determination
            if result.status == ImportStatus.PROCESSING:
                if result.error_count == 0:
                    result.status = ImportStatus.SUCCESS
                elif result.successful_contacts > 0:
                    result.status = ImportStatus.SUCCESS  # Partial success
                else:
                    result.status = ImportStatus.FAILED
            
            # Final import job update
            end_time = datetime.now(timezone.utc)
            total_time = (end_time - result.start_time).total_seconds()
            
            await self._update_import_job(import_job_id, {
                'status': result.status,
                'rows_processed': result.processed_rows,
                'completed_at': end_time,
                'errors': [error.dict() for error in result.errors[:self.config.ERROR_SAMPLE_SIZE]]
            })
            
            logger.info(
                f"Enhanced CSV parse complete for job {import_job_id}: "
                f"{result.successful_contacts} contacts, {result.error_count} errors, "
                f"{total_time:.1f}s total time"
            )
            
        except Exception as e:
            logger.error(f"Enhanced CSV parse failed for job {import_job_id}: {str(e)}")
            result.status = ImportStatus.FAILED
            
            # Update import job with failure
            await self._update_import_job(import_job_id, {
                'status': ImportStatus.FAILED,
                'completed_at': datetime.now(timezone.utc),
                'errors': [{'row': 0, 'column': None, 'message': str(e), 'value': None}]
            })
            
            raise
        
        return result
    

    async def parse_file_with_mapping(
    self,
    file_path: Path,
    import_job_id: str,
    mapping_config: Dict[str, Any],
    progress_callback: Optional[callable] = None
) -> CSVParseResult:
        """
        Parse CSV file with explicit user-provided column mapping.
        
        This method bypasses auto-detection and uses the exact columns specified by the user.
        
        Args:
            file_path: Path to the CSV file to parse
            import_job_id: ID of the import job tracking this operation
            mapping_config: User-provided column mapping configuration
            progress_callback: Optional callback for progress updates
            
        Returns:
            CSVParseResult: Results of the parsing operation
        """
        logger.info(f"Starting mapped CSV parse for import job {import_job_id}")
        result = CSVParseResult()
        result.start_time = datetime.now(timezone.utc)
        
        try:
            # Phase 1: File validation and setup (same as before)
            await self._validate_file(file_path)
            result.sha256_hash = await self._calculate_file_hash(file_path)
            
            await self._update_import_job(import_job_id, {
                'status': ImportStatus.PROCESSING,
                'sha256': result.sha256_hash,
                'started_at': result.start_time
            })
            
            # Phase 2: File format detection
            encoding, delimiter = await self._detect_file_format(file_path)
            logger.info(f"Detected encoding: {encoding}, delimiter: '{delimiter}'")
            
            # Phase 3: Parse headers and validate mapping
            headers = await self._parse_headers(file_path, encoding, delimiter)
            
            # Validate that all mapped columns exist
            all_mapped_columns = (
                mapping_config.get('phone_columns', []) +
                [mapping_config.get('name_column')] +
                mapping_config.get('skip_columns', []) +
                mapping_config.get('tag_columns', [])
            )
            
            for col in all_mapped_columns:
                if col and col not in headers:
                    raise ValidationError(f"Column '{col}' not found in CSV. Available columns: {', '.join(headers)}")
            
            # Phase 4: Create synthetic detection result from mapping
            result.column_detection = self._create_detection_from_mapping(mapping_config, headers)
            
            # Phase 5: Count rows
            result.total_rows = await self._count_csv_rows(file_path, encoding)
            logger.info(f"Total rows to process: {result.total_rows}")
            
            if result.total_rows > self.config.MAX_ROWS:
                raise ValidationError(
                    f"File has {result.total_rows:,} rows, maximum allowed is {self.config.MAX_ROWS:,}"
                )
            
            # Phase 6: Process with mapped columns
            async for chunk_result in self._process_csv_chunks_with_mapping(
                file_path, encoding, delimiter, import_job_id, mapping_config, result
            ):
                result.processed_rows += chunk_result['processed']
                result.successful_contacts += chunk_result['successful']
                result.errors.extend(chunk_result['errors'])
                result.processing_rate = chunk_result.get('processing_rate', 0)
                result.memory_usage_mb = chunk_result.get('memory_usage_mb', 0.0)
                
                # Progress reporting (same as before)
                current_time = datetime.now(timezone.utc).timestamp()
                if (current_time - self._last_progress_update) >= self.config.PROGRESS_UPDATE_INTERVAL:
                    await self._update_import_job(import_job_id, {
                        'rows_processed': result.processed_rows,
                        'errors': [error.dict() for error in result.errors[:self.config.ERROR_SAMPLE_SIZE]]
                    })
                    
                    if progress_callback:
                        # Same progress callback logic as before
                        pass
                    
                    self._last_progress_update = current_time
                
                if result.has_critical_errors:
                    logger.warning(f"Too many errors ({result.error_count}), stopping import")
                    result.status = ImportStatus.FAILED
                    break
            
            # Phase 7: Final status determination (same as before)
            if result.status == ImportStatus.PROCESSING:
                if result.error_count == 0:
                    result.status = ImportStatus.SUCCESS
                elif result.successful_contacts > 0:
                    result.status = ImportStatus.SUCCESS
                else:
                    result.status = ImportStatus.FAILED
            
            # Final update
            end_time = datetime.now(timezone.utc)
            total_time = (end_time - result.start_time).total_seconds()
            
            await self._update_import_job(import_job_id, {
                'status': result.status,
                'rows_processed': result.processed_rows,
                'completed_at': end_time,
                'errors': [error.dict() for error in result.errors[:self.config.ERROR_SAMPLE_SIZE]]
            })
            
            logger.info(
                f"Mapped CSV parse complete for job {import_job_id}: "
                f"{result.successful_contacts} contacts, {result.error_count} errors, "
                f"{total_time:.1f}s total time"
            )
            
        except Exception as e:
            logger.error(f"Mapped CSV parse failed for job {import_job_id}: {str(e)}")
            result.status = ImportStatus.FAILED
            
            await self._update_import_job(import_job_id, {
                'status': ImportStatus.FAILED,
                'completed_at': datetime.now(timezone.utc),
                'errors': [{'row': 0, 'column': None, 'message': str(e), 'value': None}]
            })
            
            raise
        
        return result
    
    async def _enhanced_column_detection(
        self,
        file_path: Path,
        encoding: str,
        delimiter: str,
        headers: List[str]
    ) -> ColumnDetectionResult:
        """
        Enhanced column detection with confidence scoring and user guidance.
        
        Args:
            file_path: Path to CSV file
            encoding: File encoding
            delimiter: CSV delimiter
            headers: Parsed headers
            
        Returns:
            ColumnDetectionResult: Comprehensive detection results with confidence scores
        """
        logger.info("Starting enhanced column detection")
        result = ColumnDetectionResult()
        
        # Sample data for analysis
        buckets = [[] for _ in headers]
        
        with open(file_path, "r", encoding=encoding) as f:
            reader = csv.reader(f, delimiter=delimiter)
            next(reader)  # Skip header
            
            for i, row in enumerate(reader, start=1):
                if i > self.config.COLUMN_SAMPLE_SIZE:
                    break
                for idx, cell in enumerate(row):
                    if idx < len(buckets):  # Safety check
                        buckets[idx].append(cell)
        
        # Enhanced phone column detection
        phone_scores = {}
        for i, header in enumerate(headers):
            score = _enhanced_phone_column_score(header, buckets[i])
            phone_scores[header] = score
            logger.debug(f"Phone score for '{header}': {score:.1f}")
        
        # Sort phone candidates by score
        phone_candidates = sorted(phone_scores.items(), key=lambda x: x[1], reverse=True)
        result.phone_candidates = [
            {"column": col, "score": score, "confidence": self._score_to_confidence(score)}
            for col, score in phone_candidates[:self.config.MAX_PHONE_CANDIDATES]
            if score > 0
        ]
        
        # Select primary phone column
        if phone_candidates and phone_candidates[0][1] >= self.config.MIN_PHONE_CONFIDENCE:
            result.primary_phone_column = phone_candidates[0][0]
            result.confidence_scores["phone"] = phone_candidates[0][1]
        
        # Enhanced name column detection
        name_scores = {}
        for i, header in enumerate(headers):
            score = _enhanced_name_column_score(header, buckets[i])
            name_scores[header] = score
            logger.debug(f"Name score for '{header}': {score:.1f}")
        
        # Select best name column
        best_name_candidate = max(name_scores.items(), key=lambda x: x[1], default=(None, 0))
        if best_name_candidate[1] >= self.config.MIN_NAME_CONFIDENCE:
            result.name_column = best_name_candidate[0]
            result.confidence_scores["name"] = best_name_candidate[1]
        
        # Build detected columns mapping (backwards compatibility)
        phone_cols = [col for col, score in phone_candidates if score > 0]
        result.detected_columns = {
            "phones": phone_cols,
            "primary_phone": result.primary_phone_column,
            "name": result.name_column,
            "confidence_scores": result.confidence_scores
        }
        
        # Determine detection quality and generate user guidance
        result.detection_quality = self._assess_detection_quality(result)
        result.user_guidance = self._generate_user_guidance(result, headers)
        
        logger.info(f"Column detection complete: {result.detection_quality} confidence")
        logger.info(f"Primary phone: {result.primary_phone_column} ({result.phone_confidence:.1f})")
        logger.info(f"Name column: {result.name_column} ({result.name_confidence:.1f})")
        
        return result
    
    def _score_to_confidence(self, score: float) -> str:
        """Convert numeric score to confidence level."""
        if score >= 80:
            return "high"
        elif score >= 50:
            return "medium"
        elif score >= 20:
            return "low"
        else:
            return "very_low"
    
    def _assess_detection_quality(self, detection: ColumnDetectionResult) -> str:
        """Assess overall detection quality for user feedback."""
        phone_conf = detection.phone_confidence
        
        if phone_conf >= 80:
            return "high"
        elif phone_conf >= 50:
            return "medium"
        elif phone_conf >= 20:
            return "low"
        else:
            return "very_low"
    
    def _generate_user_guidance(self, detection: ColumnDetectionResult, headers: List[str]) -> List[str]:
        """Generate user guidance based on detection results."""
        guidance = []
        
        if detection.detection_quality == "very_low":
            guidance.append("❌ Could not reliably detect phone number column. Please verify your CSV format.")
            guidance.append("📋 Available columns: " + ", ".join(headers))
            
        elif detection.detection_quality == "low":
            guidance.append("⚠️ Low confidence in phone number detection. Please review the selected column.")
            if detection.primary_phone_column:
                guidance.append(f"📱 Detected phone column: '{detection.primary_phone_column}'")
                
        elif detection.detection_quality == "medium":
            guidance.append("✅ Phone column detected with medium confidence.")
            if detection.primary_phone_column:
                guidance.append(f"📱 Using column: '{detection.primary_phone_column}'")
                
        else:  # high confidence
            guidance.append("✅ Phone column detected with high confidence.")
            if detection.primary_phone_column:
                guidance.append(f"📱 Using column: '{detection.primary_phone_column}'")
        
        # Name column guidance
        if not detection.name_column:
            guidance.append("ℹ️ No name column detected. Contacts will be created with phone numbers only.")
        else:
            guidance.append(f"👤 Name column: '{detection.name_column}'")
        
        # Additional candidates
        if len(detection.phone_candidates) > 1:
            other_candidates = [c["column"] for c in detection.phone_candidates[1:3]]
            guidance.append(f"🔄 Alternative phone columns: {', '.join(other_candidates)}")
        
        return guidance
    
    async def _enhanced_process_csv_chunks(
        self,
        file_path: Path,
        encoding: str,
        delimiter: str,
        import_job_id: str,
        result: CSVParseResult
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Enhanced CSV processing with performance monitoring."""
        
        with open(file_path, 'r', encoding=encoding) as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            
            chunk_contacts = []
            chunk_errors = []
            row_number = 1
            chunk_start_time = datetime.now(timezone.utc)
            
            for row in reader:
                row_number += 1
                
                try:
                    # Parse contact using enhanced detection results
                    contact_data = await self._enhanced_parse_contact_row(
                        row, row_number, result.column_detection, import_job_id
                    )
                    
                    if contact_data:
                        chunk_contacts.append(contact_data)
                    
                except ValidationError as e:
                    chunk_errors.append(ImportError(
                        row=row_number,
                        column=None,
                        message=str(e),
                        value=str(row)
                    ))
                
                # Process chunk when full
                if (row_number - 1) % self.config.BULK_INSERT_SIZE == 0:
                    successful = await self._bulk_insert_contacts(chunk_contacts)
                    
                    # Calculate performance metrics
                    chunk_end_time = datetime.now(timezone.utc)
                    chunk_duration = (chunk_end_time - chunk_start_time).total_seconds()
                    processing_rate = self.config.BULK_INSERT_SIZE / chunk_duration if chunk_duration > 0 else 0
                    
                    yield {
                        'processed': self.config.BULK_INSERT_SIZE,
                        'successful': successful,
                        'errors': chunk_errors,
                        'processing_rate': processing_rate,
                        'memory_usage_mb': self._get_memory_usage_mb()
                    }
                    
                    # Reset for next chunk
                    chunk_contacts.clear()
                    chunk_errors.clear()
                    chunk_start_time = chunk_end_time
                    
                    # Brief pause to prevent database overwhelming
                    await asyncio.sleep(0.01)
            
            # Process final chunk
            if chunk_contacts or chunk_errors:
                successful = await self._bulk_insert_contacts(chunk_contacts)
                remainder = (row_number - 1) % self.config.BULK_INSERT_SIZE
                
                chunk_end_time = datetime.now(timezone.utc)
                chunk_duration = (chunk_end_time - chunk_start_time).total_seconds()
                processing_rate = remainder / chunk_duration if chunk_duration > 0 else 0
                
                yield {
                    'processed': remainder or self.config.BULK_INSERT_SIZE,
                    'successful': successful,
                    'errors': chunk_errors,
                    'processing_rate': processing_rate,
                    'memory_usage_mb': self._get_memory_usage_mb()
                }
    
    async def _enhanced_parse_contact_row(
        self,
        row: Dict[str, str],
        row_number: int,
        detection: ColumnDetectionResult,
        import_job_id: str
    ) -> Optional[Contact]:
        """Enhanced contact parsing using detection results."""
        
        # Extract phone number using enhanced detection
        phone_raw: Optional[str] = None
        
        # Try primary phone column first
        if detection.primary_phone_column and detection.primary_phone_column in row:
            phone_raw = row[detection.primary_phone_column].strip()
        
        # Fallback to other phone candidates if primary is empty
        if not phone_raw:
            for candidate in detection.phone_candidates:
                col = candidate["column"]
                if col in row:
                    val = row[col].strip()
                    if val:
                        phone_raw = val
                        break
        
        if not phone_raw:
            raise ValidationError("No phone number found in detected phone columns")
        
        # Enhanced phone validation with better error messages
        try:
            is_valid, phone_normalized, error, metadata = validate_phone(phone_raw)
            if not is_valid:
                raise ValidationError(f"Invalid phone number '{phone_raw}': {error or 'Unknown validation error'}")
        except Exception as e:
            raise ValidationError(f"Phone number validation failed for '{phone_raw}': {str(e)}")
        
        # Extract name using enhanced detection
        name = None
        if detection.name_column and detection.name_column in row:
            name_raw = row[detection.name_column].strip()
            if name_raw and len(name_raw) <= 100:  # Reasonable name length limit
                name = name_raw
        
        # Enhanced tag extraction (exclude detected columns)
        tags = []
        exclude_cols = set()
        if detection.primary_phone_column:
            exclude_cols.add(detection.primary_phone_column)
        if detection.name_column:
            exclude_cols.add(detection.name_column)
        
        for key, value in row.items():
            if key not in exclude_cols and value and value.strip():
                # Clean and limit tag length
                clean_value = value.strip()[:50]  # Limit tag value length
                tags.append(f"{key}:{clean_value}")
        
        # Create enhanced contact object
        contact = Contact(
            import_id=import_job_id,
            phone=phone_normalized,
            name=name,
            tags=tags[:20],  # Limit number of tags
            csv_row_number=row_number,
            raw_data=dict(row)  # Store original row data
        )
        
        return contact
    
    def _get_memory_usage_mb(self) -> float:
        """Get current memory usage in MB for monitoring."""
        try:
            import psutil
            import os
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / 1024 / 1024  # Convert bytes to MB
        except ImportError:
            return 0.0  # psutil not available
        except Exception:
            return 0.0  # Error getting memory info
    
    # Existing methods with minimal changes for compatibility
    async def _validate_file(self, file_path: Path) -> None:
        """Validate file exists and meets size requirements."""
        if not file_path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")
        
        file_size = file_path.stat().st_size
        if file_size > self.config.MAX_FILE_SIZE:
            raise ValidationError(
                f"File size {file_size:,} bytes exceeds maximum allowed size {self.config.MAX_FILE_SIZE:,} bytes"
            )
        
        if file_size == 0:
            raise ValidationError("CSV file is empty")
    
    async def _calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA-256 hash of file for integrity checking."""
        hash_sha256 = hashlib.sha256()
        
        # Read file in chunks to handle large files
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_sha256.update(chunk)
        
        return hash_sha256.hexdigest()
    
    async def _detect_file_format(self, file_path: Path) -> Tuple[str, str]:
        """Detect file encoding and CSV delimiter."""
        # Try different encodings
        for encoding in self.config.ENCODING_FALLBACKS:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    # Read first few lines to detect delimiter
                    sample = f.read(8192)
                    
                    # Use csv.Sniffer to detect delimiter
                    sniffer = csv.Sniffer()
                    delimiter = sniffer.sniff(sample, delimiters=''.join(self.config.ALLOWED_DELIMITERS)).delimiter
                    
                    return encoding, delimiter
                    
            except (UnicodeDecodeError, csv.Error):
                continue
        
        # Fallback to defaults if detection fails
        logger.warning("Could not detect file format, using defaults")
        return 'utf-8', ','
    
    async def _parse_headers(self, file_path: Path, encoding: str, delimiter: str) -> List[str]:
        """Parse CSV headers and normalize column names."""
        with open(file_path, 'r', encoding=encoding) as f:
            reader = csv.reader(f, delimiter=delimiter)
            headers = next(reader)
            
            # Remove BOM if present
            cleaned_headers = []
            for i, header in enumerate(headers):
                clean_header = header.strip()
                if i == 0 and clean_header.startswith('\ufeff'):  # Remove BOM
                    clean_header = clean_header[1:]
                if clean_header:  # Only add non-empty headers
                    cleaned_headers.append(clean_header)
            
            logger.debug(f"Detected CSV headers: {cleaned_headers}")
            return cleaned_headers
    
    async def _count_csv_rows(self, file_path: Path, encoding: str) -> int:
        """Count total rows in CSV file efficiently."""
        with open(file_path, 'r', encoding=encoding) as f:
            # Skip header row
            next(f)
            row_count = sum(1 for _ in f)
        
        return row_count
    
    async def _bulk_insert_contacts(self, contacts: List[Contact]) -> int:
        """
        Enhanced bulk insert with better error handling and performance monitoring.
        
        Args:
            contacts: List of Contact objects to insert
            
        Returns:
            int: Number of contacts successfully inserted (excludes duplicates)
        """
        if not contacts:
            return 0
        
        insert_start_time = datetime.now(timezone.utc)
        
        try:
            # Use PostgreSQL's efficient UPSERT with VALUES clause
            from sqlalchemy.dialects.postgresql import insert
            from sqlalchemy import select
            
            # Prepare contact data for bulk operation
            contact_values = []
            for contact in contacts:
                # Generate ID if not set
                if not contact.id:
                    contact.id = generate_prefixed_id(IDPrefix.CONTACT)
                
                contact_values.append({
                    'id': contact.id,
                    'import_id': contact.import_id,
                    'phone': contact.phone,
                    'name': contact.name,
                    'tags': contact.tags,
                    'csv_row_number': contact.csv_row_number,
                    'raw_data': contact.raw_data,
                })
            
            # Single UPSERT operation - PostgreSQL optimized
            insert_stmt = insert(Contact.__table__)
            upsert_stmt = insert_stmt.on_conflict_do_nothing(
                index_elements=['import_id', 'phone']  # Uses existing unique constraint
            )
            
            # Execute bulk upsert
            result = await self.session.execute(upsert_stmt, contact_values)
            
            # Count successful insertions
            count_query = select(func.count(Contact.id)).where(
                Contact.id.in_([contact['id'] for contact in contact_values])
            )
            count_result = await self.session.execute(count_query)
            successful_count = count_result.scalar() or 0
            
            # Performance logging
            insert_duration = (datetime.now(timezone.utc) - insert_start_time).total_seconds()
            rate = len(contacts) / insert_duration if insert_duration > 0 else 0
            
            duplicate_count = len(contacts) - successful_count
            if duplicate_count > 0:
                logger.debug(
                    f"Bulk inserted {successful_count}/{len(contacts)} contacts "
                    f"({duplicate_count} duplicates skipped) in {insert_duration:.2f}s ({rate:.1f} contacts/sec)"
                )
            else:
                logger.debug(
                    f"Bulk inserted {successful_count} contacts in {insert_duration:.2f}s ({rate:.1f} contacts/sec)"
                )
            
            return successful_count
            
        except SQLAlchemyError as e:
            logger.error(f"Bulk insert failed: {str(e)}")
            # Fallback to individual inserts for error analysis
            logger.warning("Falling back to individual insert mode for error analysis")
            return await self._bulk_insert_contacts_fallback(contacts)
        
        except Exception as e:
            logger.error(f"Unexpected error in bulk insert: {str(e)}")
            raise

    async def _bulk_insert_contacts_fallback(self, contacts: List[Contact]) -> int:
        """
        Enhanced fallback method with better error tracking.
        
        Args:
            contacts: List of Contact objects to insert
            
        Returns:
            int: Number of contacts successfully inserted
        """
        successful_count = 0
        
        logger.info(f"Processing {len(contacts)} contacts individually for error analysis")
        
        for contact in contacts:
            try:
                # Check if contact already exists
                existing = await self.session.execute(
                    select(Contact).where(
                        Contact.import_id == contact.import_id,
                        Contact.phone == contact.phone
                    )
                )
                
                if existing.scalar_one_or_none():
                    logger.debug(f"Skipping duplicate contact: {contact.phone}")
                    continue
                
                # Generate ID if not set
                if not contact.id:
                    contact.id = generate_prefixed_id(IDPrefix.CONTACT)
                
                # Insert individual contact
                self.session.add(contact)
                successful_count += 1
                
            except SQLAlchemyError as e:
                logger.error(f"Failed to insert contact {contact.phone}: {str(e)}")
            
            except Exception as e:
                logger.error(f"Unexpected error inserting contact {contact.phone}: {str(e)}")
        
        logger.info(f"Fallback processing completed: {successful_count} contacts inserted")
        return successful_count
    
    async def _update_import_job(self, import_job_id: str, updates: Dict[str, Any]) -> None:
        """Enhanced import job updates with better error handling."""
        try:
            # Get import job
            result = await self.session.execute(
                select(ImportJob).where(ImportJob.id == import_job_id)
            )
            import_job = result.scalar_one_or_none()
            
            if not import_job:
                logger.error(f"Import job not found: {import_job_id}")
                return
            
            # Apply updates
            for key, value in updates.items():
                if hasattr(import_job, key):
                    setattr(import_job, key, value)
                else:
                    logger.warning(f"Unknown import job field: {key}")
            
            # Always update the updated_at timestamp
            import_job.updated_at = datetime.now(timezone.utc)
            
        except SQLAlchemyError as e:
            logger.error(f"Failed to update import job {import_job_id}: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error updating import job {import_job_id}: {str(e)}")

    def _create_detection_from_mapping(self, mapping_config: Dict[str, Any], headers: List[str]) -> ColumnDetectionResult:
        """
        Create a synthetic detection result from user-provided mapping.
        
        Args:
            mapping_config: User-provided mapping configuration
            headers: CSV headers
            
        Returns:
            ColumnDetectionResult: Detection result based on mapping
        """
        result = ColumnDetectionResult()
        
        # Set phone columns
        phone_columns = mapping_config.get('phone_columns', [])
        if phone_columns:
            result.primary_phone_column = phone_columns[0]
            result.phone_candidates = [
                {"column": col, "score": 100.0, "confidence": "high"}
                for col in phone_columns
            ]
            result.confidence_scores["phone"] = 100.0
        
        # Set name column
        name_column = mapping_config.get('name_column')
        if name_column:
            result.name_column = name_column
            result.confidence_scores["name"] = 100.0
        
        # Set detection quality
        result.detection_quality = "high"  # User-provided mapping is always high confidence
        result.user_guidance = ["✅ Using user-provided column mapping"]
        
        result.detected_columns = {
            "phones": phone_columns,
            "primary_phone": result.primary_phone_column,
            "name": result.name_column,
            "confidence_scores": result.confidence_scores,
            "user_mapped": True  # Flag to indicate this was user-mapped
        }
        
        return result


    async def _process_csv_chunks_with_mapping(
        self,
        file_path: Path,
        encoding: str,
        delimiter: str,
        import_job_id: str,
        mapping_config: Dict[str, Any],
        result: CSVParseResult
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Process CSV chunks with explicit column mapping.
        
        Similar to _enhanced_process_csv_chunks but uses mapping config directly.
        """
        phone_columns = mapping_config.get('phone_columns', [])
        name_column = mapping_config.get('name_column')
        skip_columns = set(mapping_config.get('skip_columns', []))
        tag_columns = mapping_config.get('tag_columns', [])
        skip_invalid_phones = mapping_config.get('skip_invalid_phones', True)
        
        with open(file_path, 'r', encoding=encoding) as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            
            chunk_contacts = []
            chunk_errors = []
            row_number = 1
            chunk_start_time = datetime.now(timezone.utc)
            
            for row in reader:
                row_number += 1
                
                try:
                    # Parse contact with explicit mapping
                    contact_data = await self._parse_contact_row_with_mapping(
                        row, row_number, mapping_config, import_job_id
                    )
                    
                    if contact_data:
                        chunk_contacts.append(contact_data)
                    
                except ValidationError as e:
                    if skip_invalid_phones and "Invalid phone" in str(e):
                        # Skip invalid phones if option is set
                        logger.debug(f"Skipping row {row_number}: {str(e)}")
                    else:
                        chunk_errors.append(ImportError(
                            row=row_number,
                            column=None,
                            message=str(e),
                            value=str(row)
                        ))
                
                # Process chunk when full (same as before)
                if (row_number - 1) % self.config.BULK_INSERT_SIZE == 0:
                    successful = await self._bulk_insert_contacts(chunk_contacts)
                    
                    chunk_end_time = datetime.now(timezone.utc)
                    chunk_duration = (chunk_end_time - chunk_start_time).total_seconds()
                    processing_rate = self.config.BULK_INSERT_SIZE / chunk_duration if chunk_duration > 0 else 0
                    
                    yield {
                        'processed': self.config.BULK_INSERT_SIZE,
                        'successful': successful,
                        'errors': chunk_errors,
                        'processing_rate': processing_rate,
                        'memory_usage_mb': self._get_memory_usage_mb()
                    }
                    
                    chunk_contacts.clear()
                    chunk_errors.clear()
                    chunk_start_time = chunk_end_time
                    await asyncio.sleep(0.01)
            
            # Process final chunk
            if chunk_contacts or chunk_errors:
                successful = await self._bulk_insert_contacts(chunk_contacts)
                remainder = (row_number - 1) % self.config.BULK_INSERT_SIZE
                
                chunk_end_time = datetime.now(timezone.utc)
                chunk_duration = (chunk_end_time - chunk_start_time).total_seconds()
                processing_rate = remainder / chunk_duration if chunk_duration > 0 else 0
                
                yield {
                    'processed': remainder or self.config.BULK_INSERT_SIZE,
                    'successful': successful,
                    'errors': chunk_errors,
                    'processing_rate': processing_rate,
                    'memory_usage_mb': self._get_memory_usage_mb()
                }


    async def _parse_contact_row_with_mapping(
        self,
        row: Dict[str, str],
        row_number: int,
        mapping_config: Dict[str, Any],
        import_job_id: str
    ) -> Optional[Contact]:
        """
        Parse contact row using explicit mapping configuration.
        """
        phone_columns = mapping_config.get('phone_columns', [])
        name_column = mapping_config.get('name_column')
        skip_columns = set(mapping_config.get('skip_columns', []))
        tag_columns = mapping_config.get('tag_columns', [])
        phone_country_default = mapping_config.get('phone_country_default', 'US')
        
        # Try to get phone from mapped columns (try each until we find a valid one)
        phone_raw = None
        phone_normalized = None
        
        for phone_col in phone_columns:
            if phone_col in row:
                candidate = row[phone_col].strip()
                if candidate:
                    try:
                        is_valid, normalized, error, metadata = validate_phone(candidate, default_country=phone_country_default)
                        if is_valid:
                            phone_raw = candidate
                            phone_normalized = normalized
                            break
                    except Exception:
                        continue
        
        if not phone_normalized:
            raise ValidationError(f"No valid phone number found in columns: {', '.join(phone_columns)}")
        
        # Get name from mapped column
        name = None
        if name_column and name_column in row:
            name_raw = row[name_column].strip()
            if name_raw and len(name_raw) <= 100:
                name = name_raw
        
        # Create tags from specified columns
        tags = []
        for tag_col in tag_columns:
            if tag_col in row and row[tag_col].strip():
                clean_value = row[tag_col].strip()[:50]
                tags.append(f"{tag_col}:{clean_value}")
        
        # Create contact
        contact = Contact(
            import_id=import_job_id,
            phone=phone_normalized,
            name=name,
            tags=tags[:20],  # Limit tags
            csv_row_number=row_number,
            raw_data=dict(row)
        )
        
        return contact
# Enhanced utility functions for backward compatibility
def extract_phone_columns(headers: List[str]) -> List[str]:
    """
    Enhanced phone column extraction with better pattern matching.
    
    Args:
        headers: List of CSV headers
        
    Returns:
        List[str]: Potential phone number columns sorted by likelihood
    """
    phone_columns = []
    phone_patterns = [
        'phone', 'mobile', 'cell', 'tel', 'telephone', 'whatsapp', 
        'contact', 'number', 'fone'  # Additional patterns
    ]
    
    for header in headers:
        header_lower = header.lower().replace(' ', '_').replace('-', '_')
        if any(pattern in header_lower for pattern in phone_patterns):
            phone_columns.append(header)
    
    return phone_columns


def extract_name_columns(headers: List[str]) -> List[str]:
    """
    Enhanced name column extraction with better pattern matching.
    
    Args:
        headers: List of CSV headers
        
    Returns:
        List[str]: Potential name columns sorted by likelihood
    """
    name_columns = []
    name_patterns = [
        'name', 'contact', 'person', 'client', 'customer', 
        'full_name', 'first_name', 'last_name', 'fname', 'lname'
    ]
    
    for header in headers:
        header_lower = header.lower().replace(' ', '_').replace('-', '_')
        if any(pattern in header_lower for pattern in name_patterns):
            name_columns.append(header)
    
    return name_columns


# Estimation function for completion time
def estimate_processing_time(remaining_rows: int, processing_rate: float) -> str:
    """
    Estimate processing completion time based on current rate.
    
    Args:
        remaining_rows: Number of rows left to process
        processing_rate: Current processing rate (rows per second)
        
    Returns:
        str: Human-readable time estimate
    """
    if processing_rate <= 0:
        return "Calculating..."
    
    estimated_seconds = remaining_rows / processing_rate
    
    if estimated_seconds < 60:
        return f"~{int(estimated_seconds)} seconds"
    elif estimated_seconds < 3600:
        return f"~{int(estimated_seconds / 60)} minutes"
    else:
        return f"~{int(estimated_seconds / 3600)} hours"
