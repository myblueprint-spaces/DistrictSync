GDE 2 AdvancedCSV ETL Tool - README
Version: 1.2

This tool converts MyEducation BC General Data Extracts (GDEs) into the SpacesEDU Advanced CSV format.

Quick Start Guide
Place GDE Files
Place the following required GDE files from your MyEducation BC system into the 

data/input/ folder:


StudentSchedule.txt 

StudentDemographicEnhanced.txt 

StaffInformation.txt 

EmergencyContactInformation.txt 

CourseInformation.txt 

ClassInformationEnhanced.txt

Note: The tool requires these exact filenames and field names.

Run the Tool
Open a command line terminal, navigate to the tool's main directory, and run the appropriate command.

On Windows (PowerShell):

PowerShell

.\GDE2Acsv.exe --sis myedbc --input data\input --output data\output 




On Linux (Terminal):

Bash

./GDE2Acsv --sis myedbc --input data/input --output data/output



Get Output Files
The following five CSV files will be generated in the 

data/output/ folder:


Students.csv 


Staff.csv 


Family.csv 


Classes.csv 


Enrollments.csv 

Upload to SFTP
Upload these five files to your district's assigned SpacesEDU SFTP folder.


Configuration
All conversion settings are managed in the 

config/mappings/myedbc_mapping.yaml file.

This file controls which source files are used, maps columns, and can be edited to create dynamic student emails or define which grades are treated as homeroom-based.


Support
If you encounter any issues, please email 

Shan Peiris (shan.peiris@myblueprint.ca) with the following:

A zipped copy of your 

data/input folder.

The 

etl_tool.log file from the main directory.