// Catalog query values; network catalog search is a separate service operation.
// Defaults: Roblox Creator Docs, CatalogSearchParams datatype.
#include <string_view>
namespace catalog_search_params {
using Json=nlohmann::json;
constexpr const char* meta="LocalLuau.CatalogSearchParams";
enum Kind {String,Number,Boolean,Enumeration,EnumArray};
struct Field {const char* name;Kind kind;const char* enum_type;double initial;};
constexpr Field fields[]={
    {"SearchKeyword",String,nullptr,0},{"MinPrice",Number,nullptr,0},
    {"MaxPrice",Number,nullptr,2147483647},{"SortType",Enumeration,"CatalogSortType",1},
    {"SortAggregation",Enumeration,"CatalogSortAggregation",6},
    {"CategoryFilter",Enumeration,"CatalogCategoryFilter",1},
    {"SalesTypeFilter",Enumeration,"SalesTypeFilter",1},
    {"BundleTypes",EnumArray,"BundleType",0},{"AssetTypes",EnumArray,"AvatarAssetType",0},
    {"IncludeOffSale",Boolean,nullptr,0},{"CreatorName",String,nullptr,0},
    {"CreatorType",Enumeration,"CreatorTypeFilter",2},{"CreatorId",Number,nullptr,0},
    {"Limit",Number,nullptr,30},
};
constexpr size_t string_limit=1024*1024,array_limit=4096;
struct Value {Json properties=Json::object();};
const Field* field(std::string_view name) {
    for(const auto& f:fields)if(name==f.name)return &f;
    throw std::runtime_error("unknown CatalogSearchParams member");
}
Value* read(lua_State* L,int index) {
    if(!lua_isuserdata(L,index)||!lua_getmetatable(L,index))return nullptr;
    luaL_getmetatable(L,meta);const bool same=lua_rawequal(L,-1,-2);lua_pop(L,2);
    return same?static_cast<Value*>(lua_touserdata(L,index)):nullptr;
}
void push(lua_State* L,Value value) {
    auto* out=static_cast<Value*>(lua_newuserdatadtor(L,sizeof(Value),
        [](lua_State*,void* raw){static_cast<Value*>(raw)->~Value();}));
    new(out)Value(std::move(value));luaL_getmetatable(L,meta);lua_setmetatable(L,-2);
}
void validate_number(const Field& f,double value) {
    if(!std::isfinite(value))throw std::runtime_error("finite number required");
    if(std::strcmp(f.name,"Limit")==0&&value!=10&&value!=28&&value!=30&&value!=60&&value!=120)
        throw std::runtime_error("Limit must be 10, 28, 30, 60, or 120");
}
void validate_enum(lua_State* L,const Field& f,const Json& value) {
    if(!value.is_number_integer()||value<INT32_MIN||value>INT32_MAX||
            !world_bindings::push_geometry_enum_item(L,f.enum_type,value.get<int>()))
        throw std::runtime_error("invalid catalog enum value");
    lua_pop(L,1);
}
int construct(lua_State* L) {
    try {
        Value value;
        for(const auto& f:fields) {
            if(f.kind==String)value.properties[f.name]="";
            else if(f.kind==EnumArray)value.properties[f.name]=Json::array();
            else if(f.kind==Boolean)value.properties[f.name]=false;
            else if(f.kind==Enumeration)value.properties[f.name]=int(f.initial);
            else value.properties[f.name]=f.initial;
        }
        push(L,std::move(value));return 1;
    }catch(const std::exception& e){return luaL_error(L,"CatalogSearchParams: %s",e.what()),0;}
}
int index(lua_State* L) {
    auto* value=static_cast<Value*>(luaL_checkudata(L,1,meta));
    size_t size=0;const char* name=luaL_checklstring(L,2,&size);
    try {
        const auto& f=*field(std::string_view(name,size));const auto& item=value->properties.at(f.name);
        if(f.kind==String){const auto& text=item.get_ref<const std::string&>();lua_pushlstring(L,text.data(),text.size());}
        else if(f.kind==Number)lua_pushnumber(L,item.get<double>());
        else if(f.kind==Boolean)lua_pushboolean(L,item.get<bool>());
        else if(f.kind==Enumeration)world_bindings::push_geometry_enum_item(L,f.enum_type,item.get<int>());
        else {
            lua_createtable(L,int(item.size()),0);int i=0;
            for(const auto& number:item){world_bindings::push_geometry_enum_item(L,f.enum_type,number.get<int>());lua_rawseti(L,-2,++i);}
            // Return a frozen snapshot, not a mutable view of the backing list.
            lua_setreadonly(L,-1,true);
        }
        return 1;
    }catch(const std::exception& e){return luaL_error(L,"CatalogSearchParams: %s",e.what()),0;}
}
int newindex(lua_State* L) {
    auto* value=static_cast<Value*>(luaL_checkudata(L,1,meta));
    size_t size=0;const char* name=luaL_checklstring(L,2,&size);
    try {
        const auto& f=*field(std::string_view(name,size));Json next;
        if(f.kind==String) {
            if(lua_type(L,3)!=LUA_TSTRING)throw std::runtime_error("string required");
            size_t length=0;const char* text=lua_tolstring(L,3,&length);
            if(length>string_limit)throw std::runtime_error("catalog string exceeds host limit");
            next=std::string(text,length);
        } else if(f.kind==Number) {
            if(lua_type(L,3)!=LUA_TNUMBER)throw std::runtime_error("number required");
            double number=lua_tonumber(L,3);validate_number(f,number);next=number;
        } else if(f.kind==Boolean) {
            if(lua_type(L,3)!=LUA_TBOOLEAN)throw std::runtime_error("boolean required");
            next=bool(lua_toboolean(L,3));
        } else if(f.kind==Enumeration) {
            int number=0;if(!world_bindings::read_geometry_enum_item(L,3,f.enum_type,number))
                throw std::runtime_error("wrong catalog enum type");
            next=number;
        } else {
            if(!lua_istable(L,3))throw std::runtime_error("enum array required");
            const int length=lua_objlen(L,3);
            if(length<0||size_t(length)>array_limit)throw std::runtime_error("catalog array exceeds host limit");
            size_t entries=0;lua_pushnil(L);
            while(lua_next(L,3)) {
                const bool valid=lua_type(L,-2)==LUA_TNUMBER&&lua_tonumber(L,-2)>=1&&
                    lua_tonumber(L,-2)<=length&&std::floor(lua_tonumber(L,-2))==lua_tonumber(L,-2);
                lua_pop(L,1);if(!valid)throw std::runtime_error("dense enum array required");++entries;
            }
            if(entries!=size_t(length))throw std::runtime_error("dense enum array required");
            next=Json::array();
            for(int i=1;i<=length;++i) {
                lua_rawgeti(L,3,i);int number=0;
                const bool valid=world_bindings::read_geometry_enum_item(L,-1,f.enum_type,number);lua_pop(L,1);
                if(!valid)throw std::runtime_error("wrong catalog enum array element");
                next.push_back(number);
            }
        }
        value->properties[f.name]=std::move(next);return 0;
    }catch(const std::exception& e){return luaL_error(L,"CatalogSearchParams: %s",e.what()),0;}
}
Json encode(const Value& value) {
    Json properties=value.properties;constexpr char digits[]="0123456789abcdef";
    for(const auto& f:fields)if(f.kind==String) {
        const auto& text=value.properties.at(f.name).get_ref<const std::string&>();std::string hex;hex.reserve(text.size()*2);
        for(unsigned char c:text){hex+=digits[c>>4];hex+=digits[c&15];}
        properties[f.name]=Json{{"hex",std::move(hex)}};
    }
    return Json{{"$catalog_search_params",std::move(properties)}};
}
void decode(lua_State* L,const Json& data) {
    if(!data.is_object()||data.size()!=std::size(fields))throw std::runtime_error("invalid CatalogSearchParams fields");
    Value value;
    for(const auto& f:fields) {
        if(!data.contains(f.name))throw std::runtime_error("missing CatalogSearchParams field");
        const auto& item=data.at(f.name);
        if(f.kind==String) {
            if(!item.is_object()||item.size()!=1||!item.contains("hex")||!item["hex"].is_string())throw std::runtime_error("invalid catalog string encoding");
            const auto& hex=item["hex"].get_ref<const std::string&>();
            if(hex.size()%2||hex.size()>2*string_limit)throw std::runtime_error("invalid catalog string size");
            auto nib=[](char c){return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:-1;};
            std::string text;text.reserve(hex.size()/2);
            for(size_t i=0;i<hex.size();i+=2){int a=nib(hex[i]),b=nib(hex[i+1]);if(a<0||b<0)throw std::runtime_error("invalid catalog string hex");text+=char(a*16+b);}
            value.properties[f.name]=std::move(text);
        } else {
            if(f.kind==Number){if(!item.is_number())throw std::runtime_error("invalid catalog number");validate_number(f,item.get<double>());}
            else if(f.kind==Boolean){if(!item.is_boolean())throw std::runtime_error("invalid catalog boolean");}
            else if(f.kind==Enumeration)validate_enum(L,f,item);
            else {
                if(!item.is_array()||item.size()>array_limit)throw std::runtime_error("invalid catalog enum array");
                for(const auto& number:item)validate_enum(L,f,number);
            }
            value.properties[f.name]=item;
        }
    }
    push(L,std::move(value));
}
void install(lua_State* L) {
    luaL_newmetatable(L,meta);
    lua_pushcfunction(L,index,"__index");lua_setfield(L,-2,"__index");
    lua_pushcfunction(L,newindex,"__newindex");lua_setfield(L,-2,"__newindex");
    lua_pushliteral(L,"CatalogSearchParams");lua_setfield(L,-2,"__type");
    lua_pushliteral(L,"CatalogSearchParams");lua_setfield(L,-2,"__metatable");lua_setreadonly(L,-1,true);lua_pop(L,1);
    lua_newtable(L);lua_pushcfunction(L,construct,"new");lua_setfield(L,-2,"new");
    lua_setreadonly(L,-1,true);lua_setglobal(L,"CatalogSearchParams");
}
}
