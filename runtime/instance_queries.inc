// Snapshot/local Instance:QueryDescendants, following the public selector grammar.
// Parse the full query before inspecting the tree. No code or callbacks are run.
namespace instance_queries {
struct List;
struct Filter {
    enum Kind { Class, Name, Tag, Property, Attribute, Not, Has, All } kind;
    std::string name;
    Json value;
    bool equality=false;
    std::shared_ptr<List> nested;
};
struct Step { bool direct=false; std::vector<Filter> filters; };
using Chain=std::vector<Step>;
struct List { std::vector<Chain> chains; };

class Parser {
    const std::string& text;
    size_t at=0,filters=0,steps=0;
    static bool space(char c){return c==' '||c=='\t'||c=='\r'||c=='\n'||c=='\f';}
    static bool ident(unsigned char c){return (c>='a'&&c<='z')||(c>='A'&&c<='Z')||
        (c>='0'&&c<='9')||c=='_'||c=='-'||c>=128;}
    char peek()const{return at<text.size()?text[at]:0;}
    void whitespace(){while(space(peek()))++at;}
    [[noreturn]] void error()const{throw std::runtime_error("invalid or unsupported selector near byte "+std::to_string(at+1));}
    std::string identifier(){size_t start=at;while(ident(static_cast<unsigned char>(peek())))++at;if(start==at)error();return text.substr(start,at-start);}
    std::string quoted(){
        const char quote=text[at++];std::string result;
        while(at<text.size()){
            char c=text[at++];if(c==quote)return result;
            if(c=='\\'){
                if(at==text.size())error();c=text[at++];
                if(c=='n')c='\n';else if(c=='r')c='\r';else if(c=='t')c='\t';
                else if(c!='\\'&&c!='\''&&c!='"')error();
            }
            result.push_back(c);
        }
        error();
    }
    Json literal(){
        if(peek()=='\''||peek()=='"')return quoted();
        const size_t start=at;
        while(peek()&&!space(peek())&&peek()!=']')++at;
        if(start==at)error();const auto token=text.substr(start,at-start);
        if(token=="true")return true;if(token=="false")return false;
        if(token=="nil")error();
        const auto digit=[](char c){return c>='0'&&c<='9';};
        const bool numeric=digit(token[0])||
            (token.size()>1&&((token[0]=='.'&&digit(token[1]))||
             ((token[0]=='+'||token[0]=='-')&&(digit(token[1])||token[1]=='.'))));
        char* end=nullptr;double number=numeric?std::strtod(token.c_str(),&end):0;
        if(numeric&&end==token.c_str()+token.size()){
            if(!std::isfinite(number))error();return number;
        }
        for(unsigned char c:token)if(!ident(c))error();
        return token;
    }
    Filter filter(size_t depth){
        if(++filters>128)throw std::runtime_error("selector filter limit exceeded");
        Filter result;
        if(peek()=='*'){++at;result.kind=Filter::All;}
        else if(peek()=='.'||peek()=='#'){
            const char prefix=text[at++];result.kind=prefix=='.'?Filter::Tag:Filter::Name;result.name=identifier();
        }else if(peek()=='['){
            ++at;whitespace();result.kind=Filter::Property;
            if(peek()=='$'){++at;result.kind=Filter::Attribute;}
            result.name=identifier();whitespace();
            if(peek()=='='){++at;whitespace();result.value=literal();result.equality=true;whitespace();}
            else if(result.kind==Filter::Property)error();
            if(peek()!=']')error();++at;
        }else if(peek()==':'){
            ++at;const auto name=identifier();
            if(name!="not"&&name!="has")error();
            result.kind=name=="not"?Filter::Not:Filter::Has;
            if(peek()!='(')error();++at;
            result.nested=std::make_shared<List>(list(depth+1));
            if(peek()!=')')error();++at;
        }else {result.kind=Filter::Class;result.name=identifier();}
        return result;
    }
    Chain chain(size_t depth){
        Chain result;bool direct=false;
        if(peek()=='>'){++at;direct=true;if(peek()=='>'){++at;direct=false;}whitespace();}
        while(true){
            if(++steps>64)throw std::runtime_error("selector step limit exceeded");
            Step step;step.direct=direct;
            if(!peek()||peek()==','||peek()==')'||peek()=='>')error();
            step.filters.push_back(filter(depth));
            while(peek()=='.'||peek()=='#'||peek()=='['||peek()==':')step.filters.push_back(filter(depth));
            result.push_back(std::move(step));whitespace();
            if(peek()!='>')break;
            ++at;direct=true;if(peek()=='>'){++at;direct=false;}whitespace();
        }
        return result;
    }
    List list(size_t depth){
        if(depth>8)throw std::runtime_error("selector nesting limit exceeded");
        List result;whitespace();
        while(true){
            result.chains.push_back(chain(depth));
            if(peek()!=',')break;++at;whitespace();
        }
        return result;
    }
public:
    explicit Parser(const std::string& value):text(value){}
    List parse(){
        if(text.empty()||text.size()>4096||text.find('\0')!=std::string::npos||!valid_utf8(text.data(),text.size()))
            throw std::runtime_error("selector must be nonempty UTF-8 of at most 4096 bytes without NUL");
        List result=list(0);if(at!=text.size())error();return result;
    }
};

class Engine {
    struct MatchKey {
        Node* node;Node* scope;const Step* step;
        bool operator==(const MatchKey& other)const{return node==other.node&&scope==other.scope&&step==other.step;}
    };
    struct MatchHash {
        size_t operator()(const MatchKey& key)const{return std::hash<Node*>{}(key.node)^
            (std::hash<Node*>{}(key.scope)<<1)^(std::hash<const Step*>{}(key.step)<<2);}
    };
    World& world;
    size_t work=0;
    std::unordered_map<Node*,Node*> parents;
    std::unordered_map<Node*,std::vector<Node*>> children;
    std::unordered_map<MatchKey,bool,MatchHash> matches;
    void tick(){if(++work>2000000)throw std::runtime_error("selector work limit exceeded");}
    Node* parent(Node& node){auto it=parents.find(&node);return it==parents.end()?nullptr:it->second;}
    bool property(Node& node,const Filter& filter){
        if(filter.name=="Name")return Json(instance_name(world,node))==filter.value;
        if(filter.name=="ClassName")return Json(node.class_name)==filter.value;
        if(const Json* value=property_value(world,node,filter.name))return *value==filter.value;
        if(property_descriptor(node,filter.name))throw std::runtime_error("query property is unavailable in this snapshot");
        return false;
    }
    bool matches_filter(Node& node,const Filter& filter,Node& scope){
        tick();
        switch(filter.kind){
        case Filter::All:return true;
        case Filter::Class:return class_is_a(world,node.class_name,filter.name);
        case Filter::Name:return instance_name(world,node)==filter.name;
        case Filter::Tag:return collection_has(node,filter.name);
        case Filter::Property:return property(node,filter);
        case Filter::Attribute:{
            const auto value=node.attributes.lookup(filter.name);
            if(value)return !value->is_null()&&(!filter.equality||*value==filter.value);
            if(!node.attributes.known())throw std::runtime_error("query attribute state is unavailable in this snapshot");
            return false;
        }
        case Filter::Not:return !matches_list(node,*filter.nested,scope);
        case Filter::Has:return visit(node,[&](Node& child){return matches_list(child,*filter.nested,node);});
        }
        return false;
    }
    bool matches_chain(Node& node,const Chain& chain,size_t index,Node& scope){
        tick();if(&node==&scope||node.destroyed)return false;
        const MatchKey key{&node,&scope,&chain[index]};
        auto cached=matches.find(key);if(cached!=matches.end())return cached->second;
        const bool result=evaluate_chain(node,chain,index,scope);
        // The tree is immutable during one query, so cache both successes and
        // failures. Scope belongs in the key for relative :has() selectors.
        if(matches.size()<131072)matches.emplace(key,result);
        return result;
    }
    bool evaluate_chain(Node& node,const Chain& chain,size_t index,Node& scope){
        for(const auto& filter:chain[index].filters)if(!matches_filter(node,filter,scope))return false;
        Node* ancestor=parent(node);
        if(index==0)return !chain[0].direct||ancestor==&scope;
        if(chain[index].direct)return ancestor&&ancestor!=&scope&&matches_chain(*ancestor,chain,index-1,scope);
        std::unordered_set<Node*> seen;
        while(ancestor&&ancestor!=&scope&&seen.insert(ancestor).second){
            tick();if(matches_chain(*ancestor,chain,index-1,scope))return true;ancestor=parent(*ancestor);
        }
        return false;
    }
    bool matches_list(Node& node,const List& list,Node& scope){
        for(const auto& chain:list.chains)if(matches_chain(node,chain,chain.size()-1,scope))return true;
        return false;
    }
    template<typename Visitor> bool visit(Node& root,Visitor visitor){
        std::vector<Node*> pending;
        auto add=[&](Node* node){auto it=children.find(node);if(it!=children.end())for(auto c=it->second.rbegin();c!=it->second.rend();++c)pending.push_back(*c);};
        add(&root);std::unordered_set<Node*> seen{&root};
        while(!pending.empty()){
            Node* node=pending.back();pending.pop_back();tick();
            if(!seen.insert(node).second)continue;
            if(visitor(*node))return true;add(node);
        }
        return false;
    }
public:
    explicit Engine(World& value):world(value){
        for(const auto& id:world.order){
            tick();Node* node=find_node(world,id);if(!node||node->destroyed)continue;
            Node* p=find_node(world,parent_id(world,*node));
            if(p&&!p->destroyed){parents[node]=p;children[p].push_back(node);}
        }
    }
    std::vector<Node*> query(Node& root,const List& list){
        std::vector<Node*> result;
        if(!root.destroyed)visit(root,[&](Node& node){if(matches_list(node,list,root))result.push_back(&node);return false;});
        return result;
    }
};
}

int query_descendants(lua_State* L){
    Handle* h=checked_handle(L,1);luaL_checktype(L,2,LUA_TSTRING);
    if(lua_gettop(L)!=2){luaL_error(L,"QueryDescendants expects one selector string");return 0;}
    size_t length=0;const char* raw=lua_tolstring(L,2,&length);
    if(!length||length>4096){luaL_error(L,"QueryDescendants selector must contain 1 through 4096 bytes");return 0;}
    try{
        const std::string text(raw,length);
        const auto selector=instance_queries::Parser(text).parse();
        const auto nodes=instance_queries::Engine(*h->world).query(*h->node,selector);
        lua_createtable(L,static_cast<int>(nodes.size()),0);int index=1;
        for(auto* node:nodes){push_node(L,*h->world,node);lua_rawseti(L,-2,index++);}
        return 1;
    }catch(const std::exception& error){luaL_error(L,"QueryDescendants failed: %s",error.what());return 0;}
}
