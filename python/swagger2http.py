import requests
import json
import yaml
import argparse
from urllib.parse import urlparse

def load_swagger(source):
    """从文件或URL加载Swagger文档"""
    if source.startswith(('http://', 'https://')):
        response = requests.get(source, timeout=10)
        response.raise_for_status()
        
        # 自动检测JSON/YAML
        content_type = response.headers.get('Content-Type', '')
        if 'application/json' in content_type:
            return response.json()
        else:
            return yaml.safe_load(response.text)
    else:
        with open(source, 'r', encoding='utf-8') as f:
            if source.endswith('.json'):
                return json.load(f)
            return yaml.safe_load(f)

def get_base_url(swagger):
    """获取基础URL"""
    if 'servers' in swagger:  # OpenAPI 3.x
        server_url = swagger['servers'][0]['url']
        # 处理变量替换 (如 {protocol}://{host})
        if '{' in server_url:
            server_url = server_url.format(
                protocol='http',
                host='localhost',
                basePath=''
            )
        return server_url.rstrip('/')
    
    # Swagger 2.0
    scheme = swagger.get('schemes', ['http'])[0]
    host = swagger.get('host', 'localhost')
    base_path = swagger.get('basePath', '')
    return f"{scheme}://{host}{base_path}".rstrip('/')

def generate_example(schema):
    """根据schema生成示例数据"""
    if 'example' in schema:
        return schema['example']
    
    example = {}
    for prop, details in schema.get('properties', {}).items():
        if 'example' in details:
            example[prop] = details['example']
        elif details.get('type') == 'string':
            example[prop] = "string"
        elif details.get('type') == 'integer':
            example[prop] = 0
        elif details.get('type') == 'boolean':
            example[prop] = True
        elif details.get('type') == 'array' and 'items' in details:
            example[prop] = [generate_example(details['items'])]
        elif '$ref' in details:
            # 简单处理引用（实际实现需要解析$ref）
            example[prop] = {"$ref": details['$ref']}
    return example

def generate_default_body(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据OpenAPI schema生成带默认值的请求体
    支持嵌套对象、数组和常见类型默认值
    """
    if not schema:
        return {"key": "value"}  # 最简fallback
    
    # 处理schema引用（简化版）
    if '$ref' in schema:
        return {"$ref": schema['$ref']}
    
    # 处理不同schema类型
    schema_type = schema.get('type', 'object')
    example = {}
    
    if schema_type == 'object':
        for prop_name, prop_schema in schema.get('properties', {}).items():
            if 'example' in prop_schema:
                example[prop_name] = prop_schema['example']
            else:
                prop_type = prop_schema.get('type', 'string')
                if prop_type == 'string':
                    example[prop_name] = f"example_{prop_name}"
                elif prop_type == 'integer':
                    example[prop_name] = 0
                elif prop_type == 'boolean':
                    example[prop_name] = True
                elif prop_type == 'array':
                    example[prop_name] = [generate_default_body(prop_schema.get('items', {}))]
                elif prop_type == 'object':
                    example[prop_name] = generate_default_body(prop_schema)
    
    elif schema_type == 'array':
        return [generate_default_body(schema.get('items', {}))]
    
    return example

def generate_http(swagger, output_file):
    """生成.http文件"""
    base_url = get_base_url(swagger)
    endpoints = []
    
    for path, methods in swagger.get('paths', {}).items():
        for method, spec in methods.items():
            method = method.lower()
            if method not in ['get', 'post', 'put', 'delete', 'patch', 'head', 'options']:
                continue
            
            # 构建请求块
            request = [f"### {spec.get('summary', f'{method.upper()} {path}')}"]
            
            # 添加描述
            if 'description' in spec:
                request.append(f"# {spec['description']}")
            
            # 处理参数
            params = []
            headers = ["Content-Type: application/json"]
            body = ""
            
            # 处理安全需求
            if 'security' in spec or 'security' in swagger:
                headers.append("Authorization: Bearer {{token}}")
            
            # 收集参数
            for param in spec.get('parameters', []):
                if param['in'] == 'query':
                    params.append(f"{param['name']}={{{param['name']}}}")
                elif param['in'] == 'header':
                    headers.append(f"{param['name']}: {{{param['name']}}}")
                elif param['in'] == 'path':
                    path = path.replace(f"{{{param['name']}}}", f"{{{param['name']}}}")
            
            # 处理请求体
            if 'requestBody' in spec:  # OpenAPI 3.x
                content = spec['requestBody'].get('content', {})
                if 'application/json' in content:
                    schema = content['application/json'].get('schema', {})
                    body = json.dumps(generate_default_body(schema), indent=2)
            elif any(p['in'] == 'body' for p in spec.get('parameters', [])):  # Swagger 2.0
                body_param = next(p for p in spec.get('parameters', []) if p['in'] == 'body')
                body = json.dumps(generate_example(body_param.get('schema', {})), indent=2)
            
            # 构建URL
            # url = f"{base_url}{path}"
            url = f"{{{{baseUrl}}}}{path}"
            if params:
                url += "?" + "&".join(params)
            
            request.append(f"{method.upper()} {url}")
            request.append("\n".join(headers))
            if body:
                request.append(f"\n{body}")
            
            endpoints.append("\n".join(request))
    
    # 写入文件
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"@baseUrl = {base_url}\n")
        f.write("@token = <your_token>\n\n")
        f.write("\n\n".join(endpoints))
    
    print(f"成功生成 {output_file}，包含 {len(endpoints)} 个API端点")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='将Swagger/OpenAPI文档转换为.http文件',
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        'input',
        help='输入源，可以是:\n'
             '- 本地文件路径 (swagger.json/swagger.yaml)\n'
             '- HTTP端点 (http://localhost:18088/v3/api-docs)'
    )
    parser.add_argument(
        '-o', '--output',
        default='api.http',
        help='输出文件路径 (默认: api.http)'
    )
    
    args = parser.parse_args()
    
    try:
        data = load_swagger(args.input)
        generate_http(data, args.output)
    except requests.exceptions.RequestException as e:
        print(f"HTTP请求失败: {str(e)}")
    except json.JSONDecodeError:
        print("错误：无法解析JSON响应")
    except yaml.YAMLError:
        print("错误：无法解析YAML内容")
    except Exception as e:
        print(f"发生错误: {str(e)}")
