import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
import os

import pandas as pd

logger = logging.getLogger(__name__)


class DataTransformer:
    CEDS_MAPPING: Dict[str, str] = {
        "INFANT/TODDLER": "IT", "PRESCHOOL": "PR", "PRE-K": "PK",
        "PREKINDERGARTEN": "PK", "TK": "TK", "TRANSITIONAL KINDERGARTEN": "TK",
        "KINDERGARTEN": "KG", "K": "KG", "01": "01", "1": "01", "02": "02", "2": "02",
        "03": "03", "3": "03", "04": "04", "4": "04", "05": "05", "5": "05", "06": "06", "6": "06",
        "07": "07", "7": "07", "08": "08", "8": "08", "09": "09", "9": "09", "10": "10", "11": "11",
        "12": "12", "13": "13", "POSTSECONDARY": "PS", "UGRADED": "UG", "UNGRADED": "UG",
        "UG": "UG", "OTHER": "Other", "EL": "KG", "KF": "KG",
    }

    def __init__(self):
        self.school_year: int = 0
        self.academic_start: str = ""
        self.academic_end: str = ""
        self.homeroom_classes_df = pd.DataFrame()
        self.blended_class_map: Dict[str, str] = {}
        self.blended_class_metadata: Dict[str, Dict[str, Any]] = {}
        self.blended_teacher_map: Dict[str, List[str]] = {}

    @staticmethod
    def _truncate_name(name: str, max_len: int = 100) -> str:
        """Gracefully truncates a string to a max length, adding an ellipsis."""
        if len(name) <= max_len:
            return name
        
        # Leave space for "..."
        trunc_len = max_len - 3
        
        # Try to find the last space before the truncation point
        last_space = name.rfind(' ', 0, trunc_len)
        
        if last_space != -1:
            return name[:last_space] + "..."
        else:
            # If no space is found (one long word), do a hard truncation
            return name[:trunc_len] + "..."

    @staticmethod
    def grade_to_ceds(grade_value: Any) -> str:
        original = str(grade_value).strip().upper() if pd.notna(grade_value) else ""
        return DataTransformer.CEDS_MAPPING.get(original, "UG")

    @staticmethod
    def map_role(teaching_flag: Any) -> str:
        val = str(teaching_flag).strip().lower()
        return "teacher" if val == "y" else "administrator"

    def determine_school_year(self, all_data: Dict[str, pd.DataFrame], source_config: Any) -> int:
        normalized_sources = self.normalize_source_config(source_config)
        
        for role, filename in normalized_sources.items():
            df = all_data.get(filename)
            if df is not None and "school year" in df.columns:
                years = df["school year"].dropna().astype(str).str[:4].unique()
                if len(years) > 0:
                    try:
                        return int(years[0])
                    except ValueError:
                        pass

        now = datetime.now()
        if now.month >= 8:
            return now.year
        return now.year - 1

    def set_school_year(self, year: int) -> None:
        self.school_year = year
        self.academic_start = f"{year}-08-25"
        self.academic_end = f"{year + 1}-07-25"

    def generate_class_id(self, row: pd.Series, mt_id_col: str, append_year: bool = False) -> str:
        mt_id = row.get(mt_id_col, "")
        if mt_id and append_year:
            return f"{mt_id}_{self.school_year}"
        return mt_id

    def generate_class_name(self, row: pd.Series, teacher_flag_col: str, teacher_last_col: str, course_title_col: str, section_letter_col: str) -> str:
        course_title = str(row.get(course_title_col, row.get("title", "Unknown Course"))).strip()
        teacher_last = ""

        if teacher_flag_col and teacher_flag_col in row:
            if str(row.get(teacher_flag_col, "")).strip().lower() == "y":
                teacher_last = str(row.get(teacher_last_col, "")).strip()
        else:
            teacher_last = str(row.get(teacher_last_col, "")).strip()
            
        if pd.isna(teacher_last) or teacher_last.lower() == 'nan':
            teacher_last = ""

        section = str(row.get(section_letter_col, "")).strip()
        year = self.school_year

        parts = []
        if teacher_last:
            parts.append(teacher_last)
        parts.append(course_title)
        if section:
            if parts:
                parts[-1] = f"{parts[-1]} ({section})"
            else:
                parts.append(f"({section})")
        parts.append(str(year))

        full_name = " ".join(parts).strip()
        return self._truncate_name(full_name)

    def generate_user_role(self, row: pd.Series, staff_id_col: str, student_id_col: str) -> str:
        staff_val = row.get(staff_id_col, "")
        if pd.notna(staff_val) and str(staff_val).strip() != "":
            return "teacher"

        student_val = row.get(student_id_col, "")
        if pd.notna(student_val) and str(student_val).strip() != "":
            return "student"

        return "unknown"

    def generate_user_id(self, row: pd.Series, staff_id_col: str, student_id_col: str) -> str:
        staff_val = row.get(staff_id_col, "")
        if pd.notna(staff_val) and str(staff_val).strip() != "":
            return str(staff_val)

        student_val = row.get(student_id_col, "")
        if pd.notna(student_val) and str(student_val).strip() != "":
            return str(student_val)

        return "UNKNOWN_ID"
        
    def generate_student_email(self, row: pd.Series, format_str: str) -> str:
        try:
            row_lower = {k.lower(): v for k, v in row.to_dict().items()}
            return format_str.format(**row_lower)
        except KeyError as e:
            logger.warning(f"Could not generate email. Missing key: {e}")
            return ""

    def get_source_file(self, raw_data: Dict[str, pd.DataFrame], source_config: Any, role: str) -> pd.DataFrame:
        normalized_sources = self.normalize_source_config(source_config)
        
        filename = normalized_sources.get(role)
        if filename and filename in raw_data:
            return raw_data[filename].copy()
        
        logger.warning(f"Source file for role '{role}' not found in configuration")
        return pd.DataFrame()

    def normalize_source_config(self, source_config: Any) -> Dict[str, str]:
        normalized = {}
        
        if isinstance(source_config, dict):
            return source_config
        elif isinstance(source_config, list):
            if all(isinstance(item, dict) for item in source_config):
                for item in source_config:
                    if "role" in item and "file" in item:
                        normalized[item["role"]] = item["file"]
            elif all(isinstance(item, str) for item in source_config):
                roles = ["student_schedule", "course_info", "staff_info", "student_demographic"]
                for i, filename in enumerate(source_config):
                    if i < len(roles):
                        normalized[roles[i]] = filename
        
        return normalized
    
    def _create_blended_class_name(self, session_group: pd.DataFrame, field_map: Dict[str, Any], grade_str: str, course_code_to_title_map: Dict[str, str]) -> str:
        name_parts = []
        
        name_config = field_map.get("Name", {})
        if isinstance(name_config, dict):
            teacher_name_col = name_config.get("teacher_last_name", "teacher name").lower()
            if teacher_name_col in session_group.columns:
                teacher_name = session_group[teacher_name_col].iloc[0]
                if pd.notna(teacher_name) and str(teacher_name).strip():
                    name_parts.append(str(teacher_name).strip())

        unique_course_titles = sorted(list({
            course_code_to_title_map.get(code, "Unknown Course")
            for code in session_group['course code']
        }))
        
        if unique_course_titles:
            name_parts.append(" / ".join(unique_course_titles))

        if grade_str:
            name_parts.append(f"({grade_str})")

        name_parts.append(str(self.school_year))

        full_name = " ".join(name_parts).strip()
        
        # Fallback if the name is still empty
        if not full_name or len(name_parts) <=1:
            full_name = f"Blended Class {grade_str} {self.school_year}".strip()

        return self._truncate_name(full_name)

    def _validate_blended_class(self, session_group: pd.DataFrame, mtid_to_grade_map: Dict[str, str]) -> bool:
        if len(session_group) <= 1:
            return False

        grades = set()
        for mt_id in session_group['master timetable id']:
            grade = mtid_to_grade_map.get(mt_id)
            if grade:
                grades.add(self.grade_to_ceds(grade))

        return len(grades) >= 2

    def _get_blended_grade_range(self, session_group: pd.DataFrame, mtid_to_grade_map: Dict[str, str]) -> str:
        grades = set()
        for mt_id in session_group['master timetable id']:
            grade = mtid_to_grade_map.get(mt_id)
            if grade:
                grades.add(self.grade_to_ceds(grade))

        if not grades:
            return "" # Return empty string instead of "Multiple Grades"

        try:
            sorted_grades = sorted(grades, key=int)
        except ValueError:
            sorted_grades = sorted(grades)
        
        return "/".join(sorted_grades)

    def _detect_blended_classes(self, class_info_df: pd.DataFrame, mapping: Dict[str, Any], raw_data: Dict[str, pd.DataFrame], global_config: Dict[str, Any]) -> None:
        if class_info_df.empty:
            logger.info("No class info data available for blended class detection")
            return

        normalized_sources = self.normalize_source_config(mapping.get("source_files", {}))
        field_map = mapping.get("field_map", {})
        
        enrollment_map = global_config.get("mappings", {}).get("Enrollments", {}).get("field_map", {})
        user_id_map = enrollment_map.get("User ID", {})
        teacher_id_col = user_id_map.get("staff_id_col", "teacher id").lower()

        student_schedule_df = self.get_source_file(raw_data, normalized_sources, "student_schedule")
        course_info_df = self.get_source_file(raw_data, normalized_sources, "course_info")

        if student_schedule_df.empty or course_info_df.empty:
            logger.warning("Student schedule or course info data is missing. Cannot reliably detect blended classes.")
            return

        student_schedule_df.columns = [col.strip().lower() for col in student_schedule_df.columns]
        course_info_df.columns = [col.strip().lower() for col in course_info_df.columns]
        
        if 'master timetable id' in student_schedule_df.columns:
            student_schedule_df['master timetable id'] = student_schedule_df['master timetable id'].astype(str).str.strip()

        if 'master timetable id' in student_schedule_df.columns and 'grade' in student_schedule_df.columns:
            grade_map_df = student_schedule_df[['master timetable id', 'grade']].dropna().drop_duplicates()
            mtid_to_grade_map = pd.Series(grade_map_df.grade.values, index=grade_map_df['master timetable id']).to_dict()
        else:
            logger.warning("Missing 'master timetable id' or 'grade' in student schedule. Grade lookup will fail.")
            mtid_to_grade_map = {}

        if 'course code' in course_info_df.columns and 'title' in course_info_df.columns:
            title_map_df = course_info_df[['course code', 'title']].dropna().drop_duplicates('course code')
            course_code_to_title_map = pd.Series(title_map_df.title.values, index=title_map_df['course code']).to_dict()
        else:
            logger.warning("Missing 'course code' or 'title' in course info. Class naming may be incomplete.")
            course_code_to_title_map = {}
            
        required_for_grouping = [teacher_id_col, 'master timetable id']
        if any(col not in class_info_df.columns for col in required_for_grouping):
            logger.warning(f"Cannot detect blended classes. Missing required columns: {required_for_grouping}")
            return

        working_records = class_info_df.copy()
        
        session_components = ['school number', teacher_id_col, 'term', 'semester', 'day', 'period']
        available_components = [col for col in session_components if col in working_records.columns]
        
        for col in available_components:
            working_records[col] = working_records[col].fillna('').astype(str)
        working_records['session_key'] = working_records[available_components].agg('_'.join, axis=1)
        
        blended_class_count = 0
        for session_key, group in working_records.groupby('session_key'):
            if len(group) <= 1:
                continue
            
            is_valid_blend = self._validate_blended_class(group, mtid_to_grade_map)
            
            if is_valid_blend:
                blended_id = f"BLENDED_{session_key}_{self.school_year}"
                all_mt_ids = group['master timetable id'].tolist()
                
                for mt_id in all_mt_ids:
                    self.blended_class_map[mt_id] = blended_id
                
                all_teachers = working_records[working_records['master timetable id'].isin(all_mt_ids)][teacher_id_col].unique().tolist()
                self.blended_teacher_map[blended_id] = all_teachers
                
                grade_str = self._get_blended_grade_range(group, mtid_to_grade_map)
                class_name = self._create_blended_class_name(group, field_map, grade_str, course_code_to_title_map)
                
                self.blended_class_metadata[blended_id] = {
                    "Name": class_name,
                    "Grade": grade_str,
                    "School ID": group['school number'].iloc[0] if 'school number' in group.columns else "",
                    "Original_MT_IDs": all_mt_ids
                }
                
                blended_class_count += 1

        logger.info(f"[Blended Classes] Detection completed: {blended_class_count} blended classes identified")

    def transform(self, df: pd.DataFrame, mapping: Dict[str, Any], entity: str, raw_data: Dict[str, pd.DataFrame], global_config: Dict[str, Any]) -> pd.DataFrame:
        working = df.copy()
        result = pd.DataFrame()
        working.columns = [col.strip().lower() for col in working.columns]
        field_map = mapping.get("field_map", {})
        
        if entity == "Students":
            email_format_config = field_map.get("Email Address", {})
            if isinstance(email_format_config, dict):
                email_format = email_format_config.get("format")
                if email_format:
                    result["Email Address"] = working.apply(self.generate_student_email, format_str=email_format.lower(), axis=1)

            if "EnrollStatus" not in result.columns:
                if "enrolment status" in working.columns:
                    result["EnrollStatus"] = working["enrolment status"].apply(
                        lambda x: str(x).strip() if str(x).strip() in ["Active", "PreReg"] else "Inactive"
                    )
                elif "withdraw date" in working.columns:
                    result["EnrollStatus"] = working["withdraw date"].apply(
                        lambda x: "Active" if pd.isna(x) or str(x).strip() == "" else "Inactive"
                    )
                else:
                    logger.warning("[Students] Could not find 'enrolment status' or 'withdraw date' column. Defaulting to 'Active'.")
                    result["EnrollStatus"] = "Active"

        if entity == "Staff":
            source_config = mapping.get("source_files", {})
            normalized_sources = self.normalize_source_config(source_config)
            
            enrollment_map = global_config.get("mappings", {}).get("Enrollments", {}).get("field_map", {})
            user_id_map = enrollment_map.get("User ID", {})
            teacher_id_col = user_id_map.get("staff_id_col", "teacher id").lower()
            
            if len(normalized_sources) > 1:
                staff_filename = normalized_sources.get("staff_info", "")
                roster_filename = list(normalized_sources.values())[1] if len(normalized_sources) > 1 else ""
                
                staff_df = raw_data.get(staff_filename, pd.DataFrame())
                roster_df = raw_data.get(roster_filename, pd.DataFrame())

                if not staff_df.empty and not roster_df.empty and teacher_id_col in staff_df.columns and "staff sourceid" in roster_df.columns:
                    working = staff_df.merge(
                        roster_df[[teacher_id_col, "staff sourceid"]].drop_duplicates(teacher_id_col),
                        on=teacher_id_col,
                        how="left",
                    )
                    working.columns = [col.strip().lower() for col in working.columns]

        if entity == "Classes":
            source_config = mapping.get("source_files", {})
            normalized_sources = self.normalize_source_config(source_config)
            homeroom_grades = global_config.get("homeroom_grades", [])
            final_classes = []

            enrollment_map = global_config.get("mappings", {}).get("Enrollments", {}).get("field_map", {})
            user_id_map = enrollment_map.get("User ID", {})
            teacher_id_col = user_id_map.get("staff_id_col", "teacher id").lower()

            class_info_df = self.get_source_file(raw_data, normalized_sources, "class_info")
            if not class_info_df.empty:
                class_info_df.columns = [col.strip().lower() for col in class_info_df.columns]
                if teacher_id_col in class_info_df.columns:
                    class_info_df[teacher_id_col] = class_info_df[teacher_id_col].astype(str).str.strip()
                if 'master timetable id' in class_info_df.columns:
                    class_info_df['master timetable id'] = class_info_df['master timetable id'].astype(str).str.strip()

                logger.info(f"[Classes] Class info data loaded: {len(class_info_df)} records")
                self._detect_blended_classes(class_info_df, mapping, raw_data, global_config)
            else:
                logger.info("[Classes] No class info data found for blended class detection")

            student_demo_df = self.get_source_file(raw_data, normalized_sources, "student_demographic")
            if not student_demo_df.empty:
                student_demo_df.columns = [col.strip().lower() for col in student_demo_df.columns]
                if teacher_id_col in student_demo_df.columns:
                    student_demo_df[teacher_id_col] = student_demo_df[teacher_id_col].astype(str).str.strip()
                    
                students_mapping = global_config.get("mappings", {}).get("Students", {})
                students_field_map = students_mapping.get("field_map", {})
                grade_config = students_field_map.get("Grade", {})
                grade_col = grade_config.get("column", "grade").lower() if isinstance(grade_config, dict) else "grade"
                student_demo_df[grade_col] = student_demo_df[grade_col].apply(self.grade_to_ceds)
                homeroom_mask = student_demo_df[grade_col].isin(homeroom_grades)
                homeroom_students = student_demo_df[homeroom_mask]

                if not homeroom_students.empty:
                    homeroom_col = students_field_map.get("Homeroom", "homeroom").lower()
                    
                    dedup_cols = ["school number", homeroom_col]
                    if teacher_id_col in homeroom_students.columns:
                        dedup_cols.append(teacher_id_col)
                    unique_homerooms = homeroom_students.drop_duplicates(subset=dedup_cols)
                    
                    if not unique_homerooms.empty and homeroom_col in unique_homerooms.columns:
                        homeroom_classes = unique_homerooms.copy()
                        homeroom_classes["Class ID"] = (
                            homeroom_classes["school number"].astype(str) + "_" +
                            homeroom_classes[homeroom_col].fillna("UnassignedHomeroom").astype(str) + f"_{self.school_year}"
                        )

                        teacher_name_col = "teacher name"
                        def create_homeroom_name(row, homeroom_col, teacher_name_col, year):
                            homeroom = row[homeroom_col]
                            teacher = row[teacher_name_col]
                            has_homeroom = pd.notna(homeroom) and str(homeroom).strip() != ""
                            has_teacher = pd.notna(teacher) and str(teacher).strip() != ""
                            parts = []
                            if has_homeroom:
                                parts.append(str(homeroom))
                            else:
                                parts.append("Unassigned Homeroom")
                            if has_teacher:
                                parts.append(f"- {teacher}")
                            parts.append(f"({year})")
                            return " ".join(parts)

                        homeroom_classes["Name"] = homeroom_classes.apply(
                            lambda row: create_homeroom_name(row, homeroom_col, teacher_name_col, self.school_year),
                            axis=1
                        )
                        homeroom_classes["Grade"] = homeroom_classes[grade_col]
                        homeroom_classes["School ID"] = homeroom_classes["school number"]
                        
                        start_date_config = field_map.get("Start Date", {})
                        end_date_config = field_map.get("End Date", {})
                        
                        if isinstance(start_date_config, dict) and start_date_config.get("use_academic_year"):
                            homeroom_classes["Start Date"] = self.academic_start
                        elif isinstance(start_date_config, dict) and "value" in start_date_config:
                            homeroom_classes["Start Date"] = start_date_config["value"]
                        else:
                            homeroom_classes["Start Date"] = self.academic_start

                        if isinstance(end_date_config, dict) and end_date_config.get("use_academic_year"):
                            homeroom_classes["End Date"] = self.academic_end
                        elif isinstance(end_date_config, dict) and "value" in end_date_config:
                            homeroom_classes["End Date"] = end_date_config["value"]
                        else:
                            homeroom_classes["End Date"] = self.academic_end

                        if teacher_id_col in homeroom_classes.columns:
                            self.homeroom_classes_df = homeroom_classes[["school number", homeroom_col, "Class ID", teacher_id_col]].copy()
                        else:
                            self.homeroom_classes_df = homeroom_classes[["school number", homeroom_col, "Class ID"]].copy()

                        homeroom_output = pd.DataFrame()
                        for tgt_field in field_map.keys():
                            if tgt_field in homeroom_classes.columns:
                                homeroom_output[tgt_field] = homeroom_classes[tgt_field]
                            else:
                                homeroom_output[tgt_field] = pd.NA
                        final_classes.append(homeroom_output)
                        logger.info(f"[Classes] Created {len(homeroom_classes)} homeroom classes")

            schedule_df = self.get_source_file(raw_data, normalized_sources, "student_schedule")
            if not schedule_df.empty:
                schedule_df.columns = [col.strip().lower() for col in schedule_df.columns]
                
                if teacher_id_col in schedule_df.columns:
                    schedule_df[teacher_id_col] = schedule_df[teacher_id_col].astype(str).str.strip()
                if 'master timetable id' in schedule_df.columns:
                    schedule_df['master timetable id'] = schedule_df['master timetable id'].astype(str).str.strip()

                schedule_df["grade_ceds"] = schedule_df["grade"].apply(self.grade_to_ceds)
                non_homeroom_mask = ~schedule_df["grade_ceds"].isin(homeroom_grades)
                non_homeroom_df = schedule_df[non_homeroom_mask].copy()
                
                if not non_homeroom_df.empty:
                    course_df = self.get_source_file(raw_data, normalized_sources, "course_info")
                    staff_df = self.get_source_file(raw_data, normalized_sources, "staff_info")
                    
                    merged_df = non_homeroom_df
                    if not course_df.empty:
                        course_df.columns = [col.strip().lower() for col in course_df.columns]
                        if "district course code" in merged_df.columns:
                            merged_df.rename(columns={"district course code": "course code"}, inplace=True)
                        merged_df = merged_df.merge(
                            course_df[["school number", "course code", "title"]],
                            on=["school number", "course code"],
                            how="left"
                        )
                    
                    if not staff_df.empty:
                        staff_df.columns = [col.strip().lower() for col in staff_df.columns]
                        if teacher_id_col in staff_df.columns:
                            staff_df[teacher_id_col] = staff_df[teacher_id_col].astype(str).str.strip()
                        
                        merged_df = merged_df.merge(
                            staff_df[[teacher_id_col, "last name"]],
                            on=teacher_id_col,
                            how="left"
                        )
                    
                    id_col_config = field_map.get("Class ID", {})
                    id_col = id_col_config.get("column", "master timetable id").lower() if isinstance(id_col_config, dict) else "master timetable id"
                    
                    if id_col in merged_df.columns:
                        merged_df['Class ID'] = merged_df[id_col].astype(str).str.strip().map(self.blended_class_map)
                        fallback_ids = merged_df.apply(
                            lambda row: self.generate_class_id(row, mt_id_col=id_col, append_year=True), 
                            axis=1
                        )
                        merged_df['Class ID'] = merged_df['Class ID'].fillna(fallback_ids)
                    else:
                        merged_df['Class ID'] = merged_df.apply(
                            lambda row: self.generate_class_id(row, mt_id_col=id_col, append_year=True), 
                            axis=1
                        )
                    
                    subject_output = pd.DataFrame()
                    subject_output["Class ID"] = merged_df["Class ID"]
                    
                    name_config = field_map.get("Name", {})
                    if isinstance(name_config, dict):
                        def get_class_name(row):
                            blended_id = row["Class ID"]
                            if blended_id in self.blended_class_metadata:
                                return self.blended_class_metadata[blended_id]["Name"]
                            else:
                                teacher_flag_col = name_config.get("primary_teacher_flag", "").lower()
                                teacher_last_col = name_config.get("teacher_last_name", "last name").lower()
                                course_title_col = name_config.get("course_title", "title").lower()
                                section_letter_col = name_config.get("section_letter", "section letter").lower()
                                return self.generate_class_name(
                                    row, teacher_flag_col, teacher_last_col, course_title_col, section_letter_col
                                )
                        
                        subject_output["Name"] = merged_df.apply(get_class_name, axis=1)

                    grade_config = field_map.get("Grade", "grade")
                    
                    def get_grade(row):
                        blended_id = row["Class ID"]
                        if blended_id in self.blended_class_metadata:
                            return ""
                        else:
                            grade_col_name = ""
                            if isinstance(grade_config, dict):
                                grade_col_name = grade_config.get("column", "grade").lower()
                            elif isinstance(grade_config, str):
                                grade_col_name = grade_config.lower()
                            
                            if grade_col_name:
                                return self.grade_to_ceds(row.get(grade_col_name, ""))
                            return ""

                    subject_output["Grade"] = merged_df.apply(get_grade, axis=1)

                    school_id_config = field_map.get("School ID", {})
                    school_id_col = school_id_config.get("column", "school number").lower() if isinstance(school_id_config, dict) else "school number"
                    subject_output["School ID"] = merged_df.get(school_id_col, "")
                    
                    start_date_config = field_map.get("Start Date", {})
                    if isinstance(start_date_config, dict) and start_date_config.get("use_academic_year"):
                        subject_output["Start Date"] = self.academic_start
                    elif isinstance(start_date_config, dict) and "value" in start_date_config:
                        subject_output["Start Date"] = start_date_config["value"]
                    else:
                        subject_output["Start Date"] = self.academic_start
                    
                    end_date_config = field_map.get("End Date", {})
                    if isinstance(end_date_config, dict) and end_date_config.get("use_academic_year"):
                        subject_output["End Date"] = self.academic_end
                    elif isinstance(end_date_config, dict) and "value" in end_date_config:
                        subject_output["End Date"] = end_date_config["value"]
                    else:
                        subject_output["End Date"] = self.academic_end
                    
                    final_classes.append(subject_output)
                    logger.info(f"[Classes] Created {len(subject_output)} subject classes")

            if final_classes:
                 result = pd.concat(final_classes, ignore_index=True).drop_duplicates(subset=["Class ID"])
                 logger.info(f"[Classes] Total classes created: {len(result)}")
                 return result
            
            return pd.DataFrame()

        if entity == "Enrollments":
            source_config = mapping.get("source_files", {})
            normalized_sources = self.normalize_source_config(source_config)
            
            schedule_df = self.get_source_file(raw_data, normalized_sources, "student_schedule")
            if schedule_df.empty:
                return pd.DataFrame()
            
            schedule_df.columns = [col.strip().lower() for col in schedule_df.columns]
            homeroom_grades = global_config.get("homeroom_grades", [])
            final_enrollments = []

            user_id_config = field_map.get("User ID", {})
            student_id_col = user_id_config.get("student_id_col", "student number").lower()
            staff_id_col = user_id_config.get("staff_id_col", "teacher id").lower()
            
            student_demo_df = self.get_source_file(raw_data, normalized_sources, "student_demographic")
            if student_demo_df.empty:
                logger.warning("[Enrollments] Student demographic data not available")
            else:
                student_demo_df.columns = [col.strip().lower() for col in student_demo_df.columns]
                if staff_id_col in student_demo_df.columns:
                    student_demo_df[staff_id_col] = student_demo_df[staff_id_col].astype(str).str.strip()
            
            if not student_demo_df.empty and not self.homeroom_classes_df.empty:
                students_mapping = global_config.get("mappings", {}).get("Students", {})
                students_field_map = students_mapping.get("field_map", {})
                grade_col = students_field_map.get("Grade", {}).get("column", "grade").lower()
                homeroom_col = students_field_map.get("Homeroom", "homeroom").lower()
                
                if grade_col in student_demo_df.columns:
                    student_demo_df[grade_col] = student_demo_df[grade_col].apply(self.grade_to_ceds)
                
                homeroom_mask = student_demo_df[grade_col].isin(homeroom_grades)
                homeroom_students = student_demo_df[homeroom_mask]
                
                if not homeroom_students.empty:
                    try:
                        if staff_id_col in self.homeroom_classes_df.columns:
                            self.homeroom_classes_df[staff_id_col] = self.homeroom_classes_df[staff_id_col].astype(str).str.strip()

                        homeroom_enroll_df = homeroom_students.merge(
                            self.homeroom_classes_df, 
                            on=["school number", homeroom_col], 
                            how="left"
                        )
                        
                        valid_homeroom_enrollments = homeroom_enroll_df[homeroom_enroll_df["Class ID"].notna()]
                        
                        if not valid_homeroom_enrollments.empty:
                            student_enroll = valid_homeroom_enrollments[["Class ID", student_id_col, "school number"]].copy()
                            student_enroll.rename(columns={student_id_col: "User ID"}, inplace=True)
                            student_enroll["Role"] = "student"
                            final_enrollments.append(student_enroll)
                            logger.info(f"[Enrollments] Created {len(student_enroll)} student homeroom enrollments")
                            
                            teacher_id_y_col = staff_id_col + "_y"
                            if teacher_id_y_col in valid_homeroom_enrollments.columns:
                                teacher_enroll = valid_homeroom_enrollments.drop_duplicates(subset=["Class ID"])[["Class ID", teacher_id_y_col, "school number"]].copy()
                                teacher_enroll.rename(columns={teacher_id_y_col: "User ID"}, inplace=True)
                                teacher_enroll["Role"] = "teacher"
                                clean_ids = teacher_enroll["User ID"].astype(str).str.strip().str.lower()
                                teacher_enroll = teacher_enroll[teacher_enroll["User ID"].notna() & (clean_ids != "") & (clean_ids != "nan")]
                                final_enrollments.append(teacher_enroll)
                                logger.info(f"[Enrollments] Created {len(teacher_enroll)} teacher homeroom enrollments")

                    except Exception as e:
                        logger.error(f"[Enrollments] Error merging homeroom data: {e}")

            if staff_id_col in schedule_df.columns:
                schedule_df[staff_id_col] = schedule_df[staff_id_col].astype(str).str.strip()

            schedule_df["grade_ceds"] = schedule_df["grade"].apply(self.grade_to_ceds)
            non_homeroom_mask = ~schedule_df["grade_ceds"].isin(homeroom_grades)
            non_homeroom_enroll_df = schedule_df[non_homeroom_mask].copy()
            
            if not non_homeroom_enroll_df.empty:
                class_id_config = field_map.get("Class ID", {})
                mt_id_col = class_id_config.get("column", "master timetable id").lower() if isinstance(class_id_config, dict) else "master timetable id"
                
                if mt_id_col in non_homeroom_enroll_df.columns:
                    non_homeroom_enroll_df[mt_id_col] = non_homeroom_enroll_df[mt_id_col].astype(str).str.strip()
                    non_homeroom_enroll_df['Class ID'] = non_homeroom_enroll_df[mt_id_col].map(self.blended_class_map)
                    fallback_ids = non_homeroom_enroll_df.apply(
                        lambda row: self.generate_class_id(row, mt_id_col=mt_id_col, append_year=True), 
                        axis=1
                    )
                    non_homeroom_enroll_df['Class ID'] = non_homeroom_enroll_df['Class ID'].fillna(fallback_ids)
                else:
                    non_homeroom_enroll_df['Class ID'] = non_homeroom_enroll_df.apply(
                        lambda row: self.generate_class_id(row, mt_id_col=mt_id_col, append_year=True), 
                        axis=1
                    )
                
                if student_id_col in non_homeroom_enroll_df.columns and "Class ID" in non_homeroom_enroll_df.columns:
                    student_enroll = non_homeroom_enroll_df[["Class ID", student_id_col, "school number"]].copy()
                    student_enroll.rename(columns={student_id_col: "User ID"}, inplace=True)
                    student_enroll["Role"] = "student"
                    final_enrollments.append(student_enroll)
                    logger.info(f"[Enrollments] Created {len(student_enroll)} student subject enrollments")
                
                blended_teacher_enrolls = []
                for blended_id, teacher_list in self.blended_teacher_map.items():
                    school_id = self.blended_class_metadata.get(blended_id, {}).get("School ID", "")
                    for teacher_id in teacher_list:
                        blended_teacher_enrolls.append({
                            "Class ID": blended_id, "User ID": teacher_id, "Role": "teacher", "school number": school_id
                        })
                if blended_teacher_enrolls:
                    blended_df = pd.DataFrame(blended_teacher_enrolls).drop_duplicates()
                    final_enrollments.append(blended_df)
                    logger.info(f"[Enrollments] Created {len(blended_df)} blended class teacher enrollments")

                non_blended_mask = ~non_homeroom_enroll_df['Class ID'].isin(self.blended_teacher_map.keys())
                non_blended_df = non_homeroom_enroll_df[non_blended_mask]
                
                if staff_id_col in non_blended_df.columns and "Class ID" in non_blended_df.columns:
                    teacher_enroll = non_blended_df[["Class ID", staff_id_col, "school number"]].copy()
                    teacher_enroll.rename(columns={staff_id_col: "User ID"}, inplace=True)
                    teacher_enroll["Role"] = "teacher"
                    clean_ids = teacher_enroll["User ID"].astype(str).str.strip().str.lower()
                    teacher_enroll = teacher_enroll[teacher_enroll["User ID"].notna() & (clean_ids != "") & (clean_ids != "nan")]
                    final_enrollments.append(teacher_enroll)
                    logger.info(f"[Enrollments] Created {len(teacher_enroll)} teacher subject enrollments")
            
            if final_enrollments:
                result = pd.concat(final_enrollments, ignore_index=True).drop_duplicates(subset=["Class ID", "User ID", "Role"])
                if "school number" in result.columns:
                    result.rename(columns={"school number": "School ID"}, inplace=True)
                
                logger.info(f"[Enrollments] Created {len(result)} total enrollments")
                return result
        
            return pd.DataFrame()
        
        for tgt_field, src_info in field_map.items():
            try:
                if tgt_field in result.columns: continue

                if isinstance(src_info, dict) and "value" in src_info:
                    result[tgt_field] = src_info["value"]
                elif isinstance(src_info, dict) and src_info.get("use_academic_year"):
                    result[tgt_field] = self.academic_start if tgt_field == "Start Date" else self.academic_end
                elif isinstance(src_info, dict) and src_info.get("append_year_to_id"):
                    col_name = src_info.get("column", "").lower()
                    result[tgt_field] = working.apply(self.generate_class_id, mt_id_col=col_name, append_year=True, axis=1)
                elif isinstance(src_info, dict):
                    column_name = src_info.get("column", "").lower()
                    transform_name = src_info.get("transform", "")
                    if column_name in working.columns:
                        series = working[column_name]
                        if transform_name:
                            func = getattr(self, transform_name)
                            result[tgt_field] = series.apply(func)
                        else:
                            result[tgt_field] = series
                    else:
                        result[tgt_field] = pd.NA
                else:
                    col = str(src_info).lower()
                    if col in working.columns:
                        result[tgt_field] = working[col]
                    else:
                        result[tgt_field] = pd.NA

            except Exception as ex:
                logger.exception(f"Error transforming {entity}.{tgt_field}: {ex}")
                result[tgt_field] = pd.NA
        return result