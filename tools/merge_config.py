import xml.etree.ElementTree as ET
import os
import sys

project_dir = os.environ.get('SERVER_VOLUME_PROJECTS_DIR')
if not project_dir:
    print("❌ ERROR: SERVER_VOLUME_PROJECTS_DIR is not set in the environment!")
    sys.exit(1)

target_path = f"{project_dir}/camicia/config.xml"
source_path = '/tmp/config_new.xml'

if not os.path.exists(target_path) or not os.path.exists(source_path):
    print("❌ ERROR: file config.xml missing.")
    sys.exit(1)

target_tree = ET.parse(target_path)
target_root = target_tree.getroot()

source_tree = ET.parse(source_path)
source_root = source_tree.getroot()

target_config = target_root.find('config')
source_config = source_root.find('config')

if source_config is not None and target_config is not None:
    for src_node in source_config:
        tgt_node = target_config.find(src_node.tag)
        if tgt_node is not None:
            target_config.remove(tgt_node)
            target_config.append(src_node)
        else:
            target_config.append(src_node)

for tag in ['tasks', 'daemons']:
    tgt_node = target_root.find(tag)
    src_node = source_root.find(tag)
    
    if tgt_node is not None:
        target_root.remove(tgt_node)
    if src_node is not None:
        target_root.append(src_node)

target_tree.write(target_path, encoding='utf-8', xml_declaration=False)
print("✅ Merge completed!")