// Parse Roblox markup into text and presentation data. Consumers never
// interpret the authored string as HTML.
namespace rich_text {
struct Document {
    std::string text;
    Json runs=Json::array();
};

#include "rich_text_unicode.inc"

std::array<uint32_t,3> uppercase(uint32_t point) {
    const auto range=std::lower_bound(std::begin(uppercase_ranges),std::end(uppercase_ranges),point,
        [](const auto& row,uint32_t value){return row.last<value;});
    if(range!=std::end(uppercase_ranges)&&range->first<=point&&(point-range->first)%range->step==0)
        return {static_cast<uint32_t>(static_cast<int>(point)+range->delta),0,0};
    const auto expansion=std::lower_bound(std::begin(uppercase_expansions),std::end(uppercase_expansions),point,
        [](const auto& row,uint32_t value){return row.point<value;});
    if(expansion!=std::end(uppercase_expansions)&&expansion->point==point)
        return {expansion->mapped[0],expansion->mapped[1],expansion->mapped[2]};
    return {point,0,0};
}
bool combining_mark(uint32_t point) {
    const auto range=std::lower_bound(std::begin(combining_mark_ranges),std::end(combining_mark_ranges),point,
        [](const auto& row,uint32_t value){return row.last<value;});
    return range!=std::end(combining_mark_ranges)&&range->first<=point;
}
// The parser validates the entire source before walking its code points.
uint32_t next_point(std::string_view text,size_t& position) {
    const auto first=static_cast<unsigned char>(text[position++]);
    if(first<0x80)return first;
    const int remaining=first<0xe0?1:first<0xf0?2:3;
    uint32_t point=first&(0x7f>>(remaining+1));
    for(int i=0;i<remaining;++i)point=(point<<6)|(text[position++]&0x3f);
    return point;
}
void append_point(std::string& text,uint32_t point) {
    if(point<0x80) {
        text.push_back(static_cast<char>(point));
        return;
    }
    const int remaining=point<0x800?1:point<0x10000?2:3;
    const auto prefix=0xffu<<(7-remaining);
    text.push_back(static_cast<char>(prefix|(point>>(6*remaining))));
    for(int i=remaining-1;i>=0;--i)
        text.push_back(static_cast<char>(0x80|((point>>(6*i))&0x3f)));
}

bool space(char c) {return c==' '||c=='\t'||c=='\r'||c=='\n';}
bool letter(char c) {return (c>='a'&&c<='z')||(c>='A'&&c<='Z');}
bool starts(std::string_view text,std::string_view prefix) {return text.substr(0,prefix.size())==prefix;}
void trim(std::string_view& value) {
    while(!value.empty()&&space(value.front()))value.remove_prefix(1);
    while(!value.empty()&&space(value.back()))value.remove_suffix(1);
}
std::string entities(std::string_view value) {
    static constexpr std::pair<std::string_view,char> codes[]={
        {"&lt;",'<'},{"&gt;",'>'},{"&quot;",'"'},{"&apos;",'\''},{"&amp;",'&'}
    };
    std::string result;
    for(size_t i=0;i<value.size();) {
        bool decoded=false;
        if(value[i]=='&')for(const auto& [entity,character]:codes) {
            if(value.substr(i,entity.size())==entity) {
                result+=character;i+=entity.size();decoded=true;break;
            }
        }
        if(!decoded)result+=value[i++];
    }
    return result;
}
bool number(std::string_view value,double minimum,double maximum,double& output) {
    trim(value);
    if(value.empty()||value.size()>24||value.find_first_not_of("0123456789.")!=std::string_view::npos)return false;
    std::string owned(value);
    char* end=nullptr;
    output=std::strtod(owned.c_str(),&end);
    return end==owned.c_str()+owned.size()&&std::isfinite(output)&&output>=minimum&&output<=maximum;
}
bool color(std::string_view value,Json& output) {
    trim(value);
    if(value.size()==7&&value.front()=='#') {
        output=Json::array();
        for(size_t i=1;i<value.size();i+=2) {
            const auto hex=[](char c)->int {
                if(c>='0'&&c<='9')return c-'0';
                if(c>='a'&&c<='f')return c-'a'+10;
                if(c>='A'&&c<='F')return c-'A'+10;
                return -1;
            };
            int a=hex(value[i]),b=hex(value[i+1]);
            if(a<0||b<0)return false;
            output.push_back(a*16+b);
        }
        return true;
    }
    if(value.size()<6||value.substr(0,4)!="rgb("||value.back()!=')')return false;
    value.remove_prefix(4);value.remove_suffix(1);
    output=Json::array();
    for(int i=0;i<3;++i) {
        const size_t comma=value.find(',');
        if((i==2)!=(comma==std::string_view::npos))return false;
        double component=0;
        if(!number(value.substr(0,comma),0,255,component)||component!=std::floor(component))return false;
        output.push_back(static_cast<int>(component));
        if(i<2)value.remove_prefix(comma+1);
    }
    return true;
}
bool attribute(const std::string& tag,std::string_view key,std::string_view value,Json& style) {
    double numeric=0;
    if(tag=="stroke") {
        auto& stroke=style["stroke"];
        if(key=="color")return color(value,stroke["color"]);
        if(key=="thickness"||key=="transparency") {
            if(!number(value,0,key=="thickness"?1000:1,numeric))return false;
            stroke[std::string(key)]=numeric;
            return true;
        }
        if((key=="joins"&&(value=="round"||value=="bevel"||value=="miter"))||
           (key=="sizing"&&(value=="fixed"||value=="scaled"))) {
            stroke[std::string(key)]=value;
            return true;
        }
        return false;
    }
    if(key=="color")return color(value,style[tag=="mark"?"mark":"color"]);
    if(key=="transparency") {
        if(!number(value,0,1,numeric))return false;
        style[tag=="mark"?"markTransparency":"transparency"]=numeric;
        return true;
    }
    if(tag!="font")return false;
    if(key=="size") {
        if(!number(value,1,1000,numeric))return false;
        style["size"]=numeric;
        return true;
    }
    if(key=="weight") {
        if(number(value,100,900,numeric)&&std::fmod(numeric,100)==0) {
            style["weight"]=static_cast<int>(numeric);
            return true;
        }
        auto lower=[](std::string_view s){
            std::string result(s);
            for(char& c:result)if(c>='A'&&c<='Z')c+=('a'-'A');
            return result;
        };
        const auto requested=lower(value);
        for(int i=0;i<kGeometryEnums[kFontWeight].count;++i) {
            if(lower(kGeometryEnums[kFontWeight].names[i])==requested) {
                style["weight"]=geometry_numeric(kFontWeight,i);
                return true;
            }
        }
    }
    if(key=="family"&&value.size()<=512&&(starts(value,"rbxasset://fonts/families/")||starts(value,"rbxassetid://"))) {
        style["family"]=value;
        return true;
    }
    if(key=="face") {
        const auto& fonts=kGeometryEnums[kClientFont];
        for(int i=0;i<fonts.count;++i)if(value==fonts.names[i]&&geometry_numeric(kClientFont,i)!=100) {
            const auto font=authored_font_from_enum(geometry_numeric(kClientFont,i)).at("$font");
            for(const auto* field:{"family","weight","style"})style[field]=font.at(field);
            return true;
        }
    }
    return false;
}
bool attributes(std::string_view input,const std::string& tag,Json& style) {
    std::set<std::string_view> keys;
    while(true) {
        trim(input);
        if(input.empty())return true;
        size_t length=0;
        while(length<input.size()&&letter(input[length]))++length;
        if(!length)return false;
        auto key=input.substr(0,length);
        if(tag=="stroke"&&key=="th")key="thickness";
        if(tag=="stroke"&&key=="tr")key="transparency";
        if(!keys.insert(key).second)return false;
        input.remove_prefix(length);trim(input);
        if(input.empty()||input.front()!='=')return false;
        input.remove_prefix(1);trim(input);
        if(input.empty()||(input.front()!='"'&&input.front()!='\''))return false;
        char quote=input.front();input.remove_prefix(1);
        size_t end=input.find(quote);
        if(end==std::string_view::npos||!attribute(tag,key,input.substr(0,end),style))return false;
        input.remove_prefix(end+1);
        if(!input.empty()&&!space(input.front()))return false;
    }
}
std::optional<Document> parse(std::string_view source) {
    if(source.size()>4096||!valid_utf8(source.data(),source.size()))return std::nullopt;
    Document result;
    enum class Case {Original,Uppercase,SmallCaps};
    struct Level {std::string tag;Json style;Case casing=Case::Original;};
    std::vector<Level> levels{{"",Json::object()}};
    auto write_run=[&](std::string text,const Json& style) {
        if(text.empty())return true;
        result.text+=text;
        if(!result.runs.empty()&&result.runs.back()["style"]==style)
            result.runs.back()["text"].get_ref<std::string&>()+=text;
        else result.runs.push_back({{"text",std::move(text)},{"style",style}});
        return result.runs.size()<=128&&result.text.size()<=4096;
    };
    bool previous_small=false;
    auto append=[&](std::string text) {
        if(text.empty())return true;
        const auto& [tag,style,casing]=levels.back();
        if(casing==Case::Original) {
            previous_small=false;
            return write_run(std::move(text),style);
        }
        Json small_style=style;
        small_style["sizeScale"]=0.8;
        std::string segment;
        for(size_t position=0;position<text.size();) {
            const auto point=next_point(text,position);
            const auto mapped=uppercase(point);
            const bool small=casing==Case::SmallCaps&&
                (combining_mark(point)?previous_small:mapped[0]!=point||mapped[1]!=0);
            if(small!=previous_small) {
                if(!write_run(std::move(segment),previous_small?small_style:style))return false;
                segment.clear();
                previous_small=small;
            }
            append_point(segment,mapped[0]);
            for(size_t i=1;i<mapped.size()&&mapped[i];++i)append_point(segment,mapped[i]);
        }
        return write_run(std::move(segment),previous_small?small_style:style);
    };
    while(!source.empty()) {
        const auto open=source.find('<');
        if(open!=0) {
            if(!append(entities(source.substr(0,open))))return std::nullopt;
            if(open==std::string_view::npos)break;
            source.remove_prefix(open);
        }
        if(starts(source,"<!--")) {
            const auto end=source.find("-->",4);
            if(end==std::string_view::npos)return std::nullopt;
            source.remove_prefix(end+3);continue;
        }
        const auto end=source.find('>');
        if(end==std::string_view::npos||end>1024)return std::nullopt;
        auto token=source.substr(1,end-1);
        source.remove_prefix(end+1);trim(token);
        if(token=="br/"||token=="br /") {
            if(!append("\n"))return std::nullopt;
            continue;
        }
        if(!token.empty()&&token.front()=='/') {
            token.remove_prefix(1);
            if(levels.size()==1||levels.back().tag!=token)return std::nullopt;
            levels.pop_back();continue;
        }
        const auto separator=token.find_first_of(" \t\r\n");
        const std::string tag(token.substr(0,separator));
        auto args=separator==std::string_view::npos?std::string_view{}:token.substr(separator);
        auto style=levels.back().style;
        auto casing=levels.back().casing;
        if(tag=="font"||tag=="mark"||tag=="stroke") {
            if(tag=="mark") {style["mark"]=Json::array({255,255,0});style["markTransparency"]=0;}
            if(tag=="stroke")style["stroke"]={{"color",{0,0,0}},{"thickness",1},{"transparency",0},{"joins","round"},{"sizing","fixed"}};
            if(!attributes(args,tag,style))return std::nullopt;
        } else {
            if(!args.empty())return std::nullopt;
            if(tag=="b")style["weight"]=std::max(700,style.value("weight",400));
            else if(tag=="i")style["style"]=1;
            else if(tag=="u")style["underline"]=true;
            else if(tag=="s")style["strike"]=true;
            else if(tag=="uppercase"||tag=="uc")casing=Case::Uppercase;
            else if(tag=="smallcaps"||tag=="sc")casing=Case::SmallCaps;
            else return std::nullopt;
        }
        if(levels.size()>=33)return std::nullopt;
        levels.push_back({tag,std::move(style),casing});
    }
    if(levels.size()!=1||result.runs.dump().size()>12*1024)return std::nullopt;
    return result;
}
}

std::optional<std::string> text_content(World& w,const Node& node) {
    const auto* value=property_value(w,node,"Text");
    if(!value||!value->is_string())return std::nullopt;
    const auto& text=value->get_ref<const std::string&>();
    if(const auto* enabled=property_value(w,node,"RichText");enabled&&*enabled==true)
        if(auto parsed=rich_text::parse(text))return std::move(parsed->text);
    return text;
}
