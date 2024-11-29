import zipfile
import logging
import xml.etree.ElementTree as ET
from pprint import pprint

logging.basicConfig(level=logging.INFO)

MIME_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/svg+xml",
    "application/xhtml+xml",
    "application/x-dtbook+xml",
    "text/css",
    "application/xml",
    "application/x-dtbncx+xml",
    "application/vnd.ms-opentype" 
}

SUGGESTED_MIME_TYPES = {
    "application/x-font-ttf": "application/vnd.ms-opentype",
    "application/vnd.adobe-page-template+xml": None
}

def verify_mimetype(epub: zipfile.ZipFile):
    namelist = epub.namelist()
    if not namelist or namelist[0] != 'mimetype':
        raise ValueError("The 'mimetype' file is not the first file in the ZIP archive.")
    
    file_info = epub.getinfo('mimetype')
    
    if file_info.compress_type != zipfile.ZIP_STORED:
        raise ValueError("The 'mimetype' file is compressed. It must be stored uncompressed.")
    
    if file_info.extra:
        raise ValueError("The 'mimetype' file contains extra fields in the ZIP header, which is not allowed.")
    
    if file_info.flag_bits & 0x1:
        raise ValueError("The 'mimetype' file is encrypted, which is not allowed.")
    
    try:
        mimetype = epub.read('mimetype').decode('ascii')
    except UnicodeDecodeError:
        raise ValueError("The 'mimetype' file is not properly encoded as ASCII.")
    
    with open(epub.filename, 'rb') as f:
        f.seek(0)
        magic_number = f.read(2)
        if magic_number != b'PK':
            raise ValueError("Invalid ZIP magic number. The file may not be a valid ZIP archive.")
        
        f.seek(30)
        header_data = f.read(8)
        if header_data != b'mimetype':
            raise ValueError("The 'mimetype' file is not correctly located or formatted.")
        
        f.seek(38)
        mimetype_data = f.read(len('application/epub+zip'))
        if mimetype_data != b'application/epub+zip':
            raise ValueError("The 'mimetype' file content is not correctly located or formatted.")
    
    logging.info("Mimetype file verification passed.")



def parse_container(epub: zipfile.ZipFile):
    try:
        container_data = epub.read('META-INF/container.xml').decode('utf-8')
    except KeyError:
        raise ValueError("Missing 'META-INF/container.xml' in EPUB file")
    except UnicodeDecodeError:
        raise ValueError("Failed to decode 'META-INF/container.xml' as UTF-8")

    try:
        namespace = {'ocf': 'urn:oasis:names:tc:opendocument:xmlns:container'}
        root = ET.fromstring(container_data)

        rootfiles = root.find('ocf:rootfiles', namespace)
        if rootfiles is None:
            raise ValueError("No <rootfiles> element found in container.xml")

        for rootfile in rootfiles.findall('ocf:rootfile', namespace):
            media_type = rootfile.get('media-type')
            if media_type == 'application/oebps-package+xml':
                full_path = rootfile.get('full-path')
                if full_path:
                    logging.info(f"EPUB Rootfile Found: {full_path}")
                    return full_path

        raise ValueError("No EPUB rootfile found in container.xml with media-type 'application/oebps-package+xml'")
    
    except ET.ParseError as e:
        raise ValueError(f"Failed to parse 'container.xml': {e}")
    

def parse_package_identity(opf_data: str):
    try:
        root = ET.fromstring(opf_data)

        if root.tag != '{http://www.idpf.org/2007/opf}package':
            raise ValueError("Root element is not 'package'")

        version = root.get('version')
        if not version:
            raise ValueError("Missing 'version' attribute in <package> element")
        else:
            logging.info(f"OPF Version: {version}")

        unique_identifier = root.get('unique-identifier')
        if not unique_identifier:
            raise ValueError("Missing 'unique-identifier' attribute in <package> element")
        else:
            logging.info(f"OPF Unique Identifier: {unique_identifier}")

        return version, unique_identifier
        

    except ET.ParseError as e:
        raise ValueError(f"Failed to parse OPF file: {e}")
    

def parse_metadata(opf_data: str, unique_identifier: str):
    try:
        root = ET.fromstring(opf_data)
        metadata = root.find('{http://www.idpf.org/2007/opf}metadata')
        if metadata is None:
            return "Error: Missing <metadata> section in OPF file"

        namespace = {'dc': 'http://purl.org/dc/elements/1.1/'}
        metadata_info = {
            'title': [e.text for e in metadata.findall('dc:title', namespace)],
            'identifier': [{
                'id': e.get('id'),
                'scheme': e.get('{http://www.idpf.org/2007/opf}scheme'),
                'value': e.text
            } for e in metadata.findall('dc:identifier', namespace)],
            'language': [e.text for e in metadata.findall('dc:language', namespace)],
            'creator': [{
                'name': e.text,
                'role': e.get('{http://www.idpf.org/2007/opf}role'),
                'file_as': e.get('{http://www.idpf.org/2007/opf}file-as')
            } for e in metadata.findall('dc:creator', namespace)],
            'publisher': [e.text for e in metadata.findall('dc:publisher', namespace)],
            'date': [{
                'date': e.text,
                'event': e.get('{http://www.idpf.org/2007/opf}event')
            } for e in metadata.findall('dc:date', namespace)],
            'subject': [e.text for e in metadata.findall('dc:subject', namespace)],
            'description': [e.text for e in metadata.findall('dc:description', namespace)],
            'rights': [e.text for e in metadata.findall('dc:rights', namespace)],
            'meta': [{
                'name': e.get('name'),
                'content': e.get('content'),
                'scheme': e.get('scheme'),
                'property': e.get('property'),
                'value': e.text
            } for e in metadata if e.tag.endswith('meta')]
        }

        identifier_ids = {item['id'] for item in metadata_info['identifier'] if item['id']}
        if unique_identifier not in identifier_ids:
            raise ValueError(f"Error: unique-identifier '{unique_identifier}' does not match any <dc:identifier> id")
        else:
            logging.info(f"unique-identifier '{unique_identifier}' matches <dc:identifier> id")
        return metadata_info
    except ET.ParseError as e:
        return f"Error: Failed to parse OPF metadata section: {e}"
    
def parse_manifest(opf_data: str, known_mime_types: set, suggested_mime_types: dict):
    try:
        root = ET.fromstring(opf_data)
        manifest = root.find('{http://www.idpf.org/2007/opf}manifest')
        if manifest is None:
            raise ValueError("Missing <manifest> section in OPF file")

        manifest_info = []
        hrefs = set()
        for item in manifest.findall('{http://www.idpf.org/2007/opf}item'):
            item_data = {
                'id': item.get('id'),
                'href': item.get('href'),
                'media-type': item.get('media-type'),
                'fallback': item.get('fallback'),
                'fallback-style': item.get('fallback-style'),
                'required-namespace': item.get('required-namespace'),
                'required-modules': item.get('required-modules')
            }

            if not item_data['id']:
                raise ValueError("Missing 'id' attribute in <item>")
            if not item_data['href']:
                raise ValueError("Missing 'href' attribute in <item>")
            if not item_data['media-type']:
                raise ValueError("Missing 'media-type' attribute in <item>")

            if item_data['href'] in hrefs:
                raise ValueError(f"Duplicate href '{item_data['href']}' found in <manifest>")
            hrefs.add(item_data['href'])

            if item_data['media-type'] not in known_mime_types:
                suggested_type = suggested_mime_types.get(item_data['media-type'])
                if suggested_type:
                    logging.warning(f"Deprecated media-type '{item_data['media-type']}' in <item> with id '{item_data['id']}'. "
                                    f"Consider using '{suggested_type}' instead.")
                else:
                    logging.warning(f"Unknown media-type '{item_data['media-type']}' in <item> with id '{item_data['id']}'.")

            manifest_info.append(item_data)

        return manifest_info

    except ET.ParseError as e:
        raise ValueError(f"Failed to parse OPF manifest section: {e}")
    
def parse_spine(opf_data: str, manifest_items: dict):
    try:
        root = ET.fromstring(opf_data)
        spine = root.find('{http://www.idpf.org/2007/opf}spine')
        if spine is None:
            raise ValueError("Missing <spine> section in OPF file")
        else:
            logging.info("Found <spine> section in OPF file")

        toc = spine.get('toc')
        if not toc:
            raise ValueError("The <spine> element is missing the 'toc' attribute")
        else:
            logging.info(f"Table of Contents ID: {toc}")

        primary_content = []
        auxiliary_content = []
        seen_idrefs = set()

        for itemref in spine.findall('{http://www.idpf.org/2007/opf}itemref'):
            idref = itemref.get('idref')
            linear = itemref.get('linear', 'yes')

            if not idref:
                raise ValueError("<itemref> missing 'idref' attribute")

            if idref in seen_idrefs:
                raise ValueError(f"Duplicate idref '{idref}' found in <spine>")
            seen_idrefs.add(idref)

            if idref not in manifest_items:
                raise ValueError(f"idref '{idref}' in <itemref> does not reference any Manifest item")

            manifest_item = manifest_items[idref]
            media_type = manifest_item.get('media-type')
            if not media_type or media_type not in {"application/xhtml+xml", "application/x-dtbook+xml", "text/x-oeb1-document"}:
                fallback = manifest_item.get('fallback')
                if not fallback or fallback not in manifest_items or manifest_items[fallback].get('media-type') not in {
                    "application/xhtml+xml", "application/x-dtbook+xml", "text/x-oeb1-document"}:
                    raise ValueError(f"Itemref idref '{idref}' does not reference a valid OPS Content Document, even with fallback")

            item_data = {'idref': idref, 'linear': linear}
            if linear == 'yes':
                primary_content.append(item_data)
            else:
                auxiliary_content.append(item_data)

        logging.info(f"Primary Content: {len(primary_content)} items")

        return {
            'toc': toc,
            'primary_content': primary_content,
            'auxiliary_content': auxiliary_content
        }

    except ET.ParseError as e:
        raise ValueError(f"Failed to parse OPF spine section: {e}")
    

def parse_guide(opf_data: str):
    try:
        root = ET.fromstring(opf_data)
        guide = root.find('{http://www.idpf.org/2007/opf}guide')
        if guide is None:
            logging.info("No <guide> section found in OPF file")
            return []
        else:
            logging.info("Found <guide> section in OPF file")

        references = []
        for ref in guide.findall('{http://www.idpf.org/2007/opf}reference'):
            ref_type = ref.get('type')
            href = ref.get('href')
            title = ref.get('title') 

            if not ref_type or not href:
                raise ValueError("Each <reference> must have 'type' and 'href' attributes")


            references.append({
                'type': ref_type,
                'href': href,
                'title': title
            })

        logging.info(f"Guide References: {len(references)}")

        return references

    except ET.ParseError as e:
        raise ValueError(f"Failed to parse OPF guide section: {e}")


    
def parse_package(epub: zipfile.ZipFile, package_content_path: str):
    try:
        opf_data = epub.read(package_content_path).decode('utf-8')
    except KeyError:
        raise ValueError(f"Missing package content file: {package_content_path}")
    except UnicodeDecodeError:
        raise ValueError(f"Failed to decode package content file as UTF-8: {package_content_path}")

    version, unique_identifier = parse_package_identity(opf_data)
    metadata = parse_metadata(opf_data, unique_identifier)
    manifest = parse_manifest(opf_data, MIME_TYPES, SUGGESTED_MIME_TYPES)
    manifest_items = {item['id']: item for item in manifest}
    spine = parse_spine(opf_data, manifest_items)
    guide = parse_guide(opf_data)
    
    # pprint(guide)

if __name__ == '__main__':
    with zipfile.ZipFile('test_data/tfs.epub') as epub:
        verify_mimetype(epub)
        package_content_path = parse_container(epub)
        parse_package(epub, package_content_path)