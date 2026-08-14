import shutil
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

# The source patch must have every top-level tag we merge, or we'd otherwise
# silently strip that tag from the live config (e.g. a malformed source with
# no <daemons> would wipe every daemon from the running project). Fail loudly
# instead of guessing.
missing = [tag for tag in ('config', 'tasks', 'daemons') if source_root.find(tag) is None]
if missing:
    print(f"❌ ERROR: source config.xml is missing top-level tag(s): {', '.join(missing)}. "
          f"Refusing to merge — this would have wiped them from the live config.")
    sys.exit(1)

# Back up the live config before touching it, so a bad merge is trivially reversible.
backup_path = target_path + '.bak'
shutil.copyfile(target_path, backup_path)

target_config = target_root.find('config')
source_config = source_root.find('config')

for src_node in source_config:
    tgt_node = target_config.find(src_node.tag)
    if tgt_node is not None:
        target_config.remove(tgt_node)
    target_config.append(src_node)

for tag in ['tasks', 'daemons']:
    tgt_node = target_root.find(tag)
    src_node = source_root.find(tag)

    if tgt_node is not None:
        target_root.remove(tgt_node)
    target_root.append(src_node)

target_tree.write(target_path, encoding='utf-8', xml_declaration=False)
print(f"✅ Merge completed! (previous config backed up to {backup_path})")