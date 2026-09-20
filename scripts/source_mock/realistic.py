"""Replace schema placeholders with coherent fictional business values offline.

This is a presentation/semantic refinement of an existing validated fixture, not
an import of real customer records. Identifiers used as application keys remain
stable; display identities are mapped consistently across all five datasets.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from .common import ROOT, CsvSink, amount, aux_fields, dec, read_csv, stable, write_json

LOCATIONS = {
    "JD": (
        "上海市",
        "上海市",
        "嘉定区",
        "310114",
        "310000",
        "310100",
        "沪0114",
        "嘉定",
        "嘉罗公路",
        "021",
    ),
    "PD": (
        "上海市",
        "上海市",
        "浦东新区",
        "310115",
        "310000",
        "310100",
        "沪0115",
        "浦东",
        "金海路",
        "021",
    ),
    "MH": (
        "上海市",
        "上海市",
        "闵行区",
        "310112",
        "310000",
        "310100",
        "沪0112",
        "闵行",
        "联航路",
        "021",
    ),
    "SZ": (
        "江苏省",
        "苏州市",
        "工业园区",
        "320571",
        "320000",
        "320500",
        "苏0591",
        "苏园",
        "星湖街",
        "0512",
    ),
    "HZ": (
        "浙江省",
        "杭州市",
        "滨江区",
        "330108",
        "330000",
        "330100",
        "浙0108",
        "杭滨",
        "江陵路",
        "0571",
    ),
    "NB": (
        "浙江省",
        "宁波市",
        "鄞州区",
        "330212",
        "330000",
        "330200",
        "浙0212",
        "鄞州",
        "首南东路",
        "0574",
    ),
}
INDUSTRIES = {
    "TECH": ("I", "65", "651", "6513", "应用软件开发"),
    "MFG": ("C", "34", "348", "3484", "机械零部件加工"),
    "TRADE": ("F", "51", "513", "5135", "厨具卫具及日用杂品批发"),
    "LOGISTICS": ("G", "58", "582", "5821", "货物运输代理"),
    "FOOD": ("C", "14", "149", "1499", "其他未列明食品制造"),
    "MEDICAL": ("C", "35", "358", "3589", "其他医疗设备及器械制造"),
}
ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)


def org_code(index):
    body = f"{70000000 + index:08d}"
    number = (
        11 - sum(int(c) * w for c, w in zip(body, (3, 7, 9, 10, 5, 8, 4, 2), strict=True)) % 11
    ) % 11
    return body + ("X" if number == 10 else str(number))


def credit_code(district, index, nonprofit=False):
    body = ("12" if nonprofit else "91") + district + org_code(index)
    return (
        body
        + ALPHABET[
            (31 - sum(ALPHABET.index(c) * w for c, w in zip(body, USCC_WEIGHTS, strict=True)) % 31)
            % 31
        ]
    )


def person_name(index):
    return (
        "赵钱孙李周吴郑王陈杨黄徐"[index % 12]
        + "明远思宁文昕瑞安嘉涵宇泽"[index // 12 % 12]
        + "博颖航悦晨欣哲轩"[index // 144 % 8]
    )


def clean(value):
    return (
        re.sub(r"[（(]\s*模拟\d*\s*[）)]", "", value)
        .replace("模拟", "")
        .replace("市场监管机构", "市场监督管理局")
        .replace("企业资讯平台", "企业财经资讯")
        .replace("产业投资机构", "嘉禾产业投资")
    )


class Profile:
    def __init__(self, tag, index):
        self.index = index
        self.original = dict(tag)
        self.tag = {k: clean(v) for k, v in tag.items()}
        key = tag["cert_district"].removeprefix("MOCK_")
        self.loc = LOCATIONS[key]
        (
            self.province,
            self.city,
            self.district,
            self.area,
            self.province_code,
            self.city_code,
            self.court_code,
            self.short,
            self.road,
            self.tel_prefix,
        ) = self.loc
        self.locality = self.city + self.district
        self.address = (
            f"{self.locality}{self.road}{80 + index % 900}号{1 + index % 8}幢{101 + index % 20}室"
        )
        self.as_of = date.fromisoformat(f"{tag['dt'][:4]}-{tag['dt'][4:6]}-{tag['dt'][6:]}")
        self.founded = date.fromisoformat(
            f"{tag['found_dt'][:4]}-{tag['found_dt'][4:6]}-{tag['found_dt'][6:]}"
        )
        self.birth = date(1969 + index % 28, 1 + index % 12, 1 + index % 28)
        self.age = (
            self.as_of.year
            - self.birth.year
            - ((self.as_of.month, self.as_of.day) < (self.birth.month, self.birth.day))
        )
        self.sex = "男" if index % 2 else "女"
        body = (
            ("320506" if self.area == "320571" else self.area)
            + self.birth.strftime("%Y%m%d")
            + f"{100 + index % 800:03d}"
        )
        self.certificate = (
            body
            + "10X98765432"[sum(int(c) * w for c, w in zip(body, ID_WEIGHTS, strict=True)) % 11]
        )
        self.spouse = person_name(index + 101)
        self.phone = f"{self.tel_prefix}-{60000000 + index:08d}"
        self.regno = self.area + f"{index:09d}"
        self.orgcode = org_code(index)
        self.credit = credit_code(self.area, index, index > 9002)
        sector = tag["industry_cd"].removeprefix("MOCK_")
        self.industry = INDUSTRIES[sector]
        self.person = clean(tag["legal_rep_nm"]) if index < 9001 else person_name(index)
        if index < 9001:
            name = clean(tag["cust_nm"])
            if index > 144:
                name = name.replace(
                    "有限公司", "恒启正和铭润丰盛安泰景瑞"[index // 144 - 1] + "有限公司"
                )
        elif index <= 9002:
            name = (
                f"{self.city.removesuffix('市')}弘泽{'商贸' if index == 9001 else '设备'}有限公司"
            )
        else:
            name = f"{self.locality}{'青禾杏林惠民博爱仁和康宁致远新辰'[(index - 9003) * 2 : (index - 9003) * 2 + 2]}公共服务中心"
        self.name = name
        self.branch = f"浦江银行{self.city.removesuffix('市')}{self.district}支行"
        self.court = f"{self.locality}人民法院"
        self.authority = f"{self.locality}市场监督管理局"
        self.tax = f"{self.locality}税务局"
        self.filed = self.as_of - timedelta(days=210 + index % 55)
        self.published = self.filed + timedelta(days=15)
        self.resolved = self.as_of - timedelta(days=15 + index % 20)
        self.case = f"（{self.filed.year}）{self.court_code}执{3000 + index}号"
        self.judgment = f"（{self.filed.year}）{self.court_code}民初{1000 + index}号"
        self.target = Decimal(20000 + stable(index, "execution") % 780000)
        self.ratio = (Decimal("0"), Decimal("0.4"), Decimal("1"))[index % 3]
        self.performed = self.target * self.ratio
        self.unperformed = self.target - self.performed
        self.penalty = Decimal(1000 + 500 * (index % 25))
        self.related_name = f"{self.city.removesuffix('市')}嘉{'辰远宁瑞安'[index % 5]}{'华正丰恒泰'[index // 5 % 5]}产业发展有限公司"
        self.related_credit = credit_code(self.area, index + 20000)
        self.related_capital = Decimal(100 + (index % 75) * 20)
        self.invest_ratio = (Decimal(".2"), Decimal(".35"), Decimal(".5"), Decimal("1"))[index % 4]
        self.tag.update(
            cust_nm=self.name,
            unify_credit_code=self.credit,
            legal_rep_nm=self.person,
            act_ctrl_psn_nm=self.person,
            busin_addr=self.address,
            cert_province=self.province_code,
            cert_city=self.city_code,
            cert_district=self.area,
            industry_cd=self.industry[0],
            industry_big_cd=self.industry[1],
            industry_mid_cd=self.industry[2],
            industry_sml_cd=self.industry[3],
        )


def business_values(p, table):
    i = p.index

    def iso(d):
        return d.isoformat()

    finish = "全部履行" if p.ratio == 1 else "部分履行" if p.ratio else "全部未履行"
    case_type = ("买卖合同纠纷", "借款合同纠纷", "承揽合同纠纷")[i % 3]
    principal = amount(p.target, 2)
    related = table in {"T_SAIC_FRPOSITION", "T_SAIC_FRINV", "T_SAIC_ENTINV"}
    result = dict(
        CUST_CODE=p.tag["cust_ind"],
        AGECLEAN=str(p.age),
        AREANAMECLEAN=p.province,
        BUSINESSENTITY=p.person,
        CARDNUM=p.certificate,
        CARDNUMCLEAN=p.certificate,
        IDNT_CERT_ID=p.certificate,
        CERNO=p.certificate,
        CASECODE=p.case,
        COURTNAME=p.court,
        DISRUPTTYPENAME=(
            "有履行能力而拒不履行生效法律文书确定义务",
            "违反财产报告制度",
            "无正当理由拒不履行执行和解协议",
        )[i % 3],
        DUTY=f"向申请执行人支付货款人民币{principal}元，承担约定利息及案件受理费。",
        EXITDATE=iso(p.resolved) if p.ratio == 1 else "",
        FOCUSNUMBER=str(2 + i % 27),
        GISTID=p.judgment,
        GISTUNIT=p.court,
        INAMECLEAN=p.person,
        INAME=p.person,
        PERFORMANCE=finish,
        PERFORMEDPART=amount(p.performed, 2),
        UNPERFORMPART=amount(p.unperformed, 2),
        PUBLISHDATECLEAN=iso(p.published),
        REGDATECLEAN=iso(p.filed),
        SEXYCLEAN=p.sex,
        SEX=p.sex,
        TYPE="自然人",
        YSFZD=p.locality,
        CASEREASON=case_type,
        CASERESULT="已结案" if p.ratio == 1 else "执行中",
        CASETIME=iso(p.filed - timedelta(days=25)),
        CASETYPE="行政处罚",
        CASEVAL=principal,
        EXESORT="行政处罚执行",
        ILLEGFACT="发布商品宣传信息时使用无法核实的销量数据。",
        ILLEGACTTYPE="发布商品宣传信息时使用无法核实的销量数据。",
        PENAM=amount(p.penalty / 10000, 4),
        PENAUTH=p.authority,
        PENAUTH_CN=p.authority,
        PENBASIS="依据广告管理相关规定作出行政处罚决定。",
        PENDECISSDATE=iso(p.filed),
        PENDECNO=f"{p.short}市监处罚〔{p.filed.year}〕{100 + i}号",
        PENEXEST="罚款已缴清，已完成整改",
        PENRESULT=f"责令改正，罚款人民币{amount(p.penalty, 2)}元。",
        PENCONTENT=f"责令改正，罚款人民币{amount(p.penalty, 2)}元。",
        PENTYPE="罚款",
        PENTYPE_CN="罚款",
        CASESTATE="执行完毕" if p.ratio == 1 else "执行中",
        EXECMONEY=principal,
        ENTSTATUS=p.tag["survival_status"],
        ENTTYPE="有限责任公司",
        ENTTYPECODE="1130",
        ESDATE=iso(p.founded),
        INDUSTRYPHYNAME=p.industry[4],
        REGCAP=amount(p.related_capital, 8),
        REGCAPCUR="CNY",
        REGNO=p.regno,
        REGORGPROVINCE=p.province,
        REGORGCITY=p.city,
        REGORGDISTRICT=p.district,
        RYNAME=p.person,
        NAME=p.person,
        POSITION="执行董事兼总经理" if i % 2 else "执行董事",
        CONFORM="货币",
        CURRENCY="CNY",
        FUNDEDRATIO=amount(p.invest_ratio, 2),
        SUBCONAM=amount(p.related_capital * p.invest_ratio, 8),
        ALTAF=p.address,
        ALTBE=f"{p.locality}{p.road}{40 + i % 30}号",
        ALTDATE=iso(p.as_of - timedelta(days=65)),
        ALTITEM="住所变更",
        ORGCODES=p.orgcode,
        ADDR=p.address,
        APPDATE=iso(p.as_of - timedelta(days=500)),
        APPPERSON=p.name,
        BEGINDATE=iso(p.as_of - timedelta(days=365)),
        ENDDATE=iso(p.as_of + timedelta(days=3287)),
        CHECKDATE=iso(p.as_of - timedelta(days=455)),
        REGDATE=iso(p.as_of - timedelta(days=365)),
        IMGNAME=f"trademark_{58000000 + i}.png",
        MARKCODE=str(58000000 + i),
        MARKCODEKEY=str(58000000 + i),
        MARKENGNAME=f"CHENGHE {i}",
        MARKNAME=f"澄禾{'甄选优品匠造新材'[(i % 4) * 2 : (i % 4) * 2 + 2]}",
        MARKIMAGE="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        MARKTYPE_NEW="文字商标",
        STATUS="有效",
        TYPEDETAILDES="广告；商业管理辅助；替他人推销",
        UNIONTYPECODE="35",
        XIANGMU_NEW="商标已注册",
        ZIPCODE=f"第{1900 + i % 120}期商标公告",
        STK_PAWN_RES="主债务已清偿",
        URL=f"https://example.com/registration/{p.area}/{i}",
        STK_PAWN_BGNR="质权人联系地址变更",
        STK_PAWN_BGRQ=iso(p.as_of - timedelta(days=60)),
        STK_PAWN_CZAMT=amount(dec(p.tag["cert_capt_amt"]) / 10000 / 10, 2),
        STK_PAWN_CZCERNO=p.certificate,
        STK_PAWN_CZPER=p.person,
        STK_PAWN_ZQCERNO=p.related_credit,
        STK_PAWN_ZQPER=p.related_name,
        STK_PAWN_REGNO=f"{p.area}{p.as_of.year}{i:05d}",
        STK_PAWN_STATUS="有效",
        FROAM=amount(p.target / 10000, 8),
        FROAUTH=p.court,
        FRODOCNO=p.case,
        FROFROM=iso(p.filed),
        FROTO=iso(p.filed + timedelta(days=1095)),
        THAWAUTH=p.court,
        THAWCOMMENT="被执行人已清偿债务，解除相关股权冻结。",
        THAWDATE=iso(p.resolved),
        THAWDOCNO=p.case + "之一",
        CONDATE=iso(p.founded + timedelta(days=30)),
        COUNTRY="中国",
        INVTYPE="自然人股东",
        SHANAME=p.person,
        JGDM=p.orgcode,
        JGDZ=p.address,
        JGMC=p.name,
        BZJG=p.authority,
        BZRQ=iso(p.founded),
        JGLX="企业法人",
        XZQH=p.area,
        ZCRQ=iso(p.founded),
        PERNAME=p.person,
        MAB_GUAR_RANGE="主债权本金、利息、违约金及实现债权的合理费用",
        MAB_DEBT_RANGE="主债权本金、利息、违约金及实现债权的合理费用",
        MAB_GUAR_TYPE="借款合同债权",
        MAB_DEBT_TYPE="借款合同债权",
        MAB_REGNO=f"{p.area}{p.as_of.year}{i:06d}",
        MAB_REG_DATE=iso(p.filed),
        MAB_REG_ORG=p.authority,
        NODENUM=p.province_code,
        MAB_PER_CERNO=p.related_credit,
        MAB_PER_CERTYPE="统一社会信用代码",
        MAB_PER_DOM=f"{p.locality}{p.road}{i % 100 + 1}号",
        MAB_PER_NAME=p.related_name,
        MAB_PAWN_DETAILS=f"数控机床{2 + i % 5}台，设备运行正常，存放于{p.address}。",
        MAB_PAWN_NAME="数控机床",
        MAB_PAWN_OWNER=p.name,
        MAB_PAWN_RMK="设备已投保，附设备清单",
        MAB_DEBT_RMK="按合同约定分期还本付息",
        MAB_CAN_RES="主债权已清偿",
        MAB_STATUS="已注销" if "CANCEL" in table else "有效",
        MAB_ALT_DETAILS="变更抵押权人联系地址，担保范围不变。",
        CLAIMTRANEE=p.related_name,
        DEBTTRANEE=p.related_name,
        LIGENTITY=p.name,
        LIGPRINCIPAL=p.person,
        LIGST="清算完结，债权债务已清理",
        LIQMEN=f"{p.person}、{p.spouse}、{person_name(i + 301)}",
        TEL=p.phone,
        RELATEENTNAME=p.related_name,
        ENTJGNAME=p.related_name,
        LEREPSIGN="1",
        RELATEREGNO=p.area + f"{i + 20000:09d}",
        REGORG=p.authority,
        REGORGCODE=p.area,
        BRADDR=f"{p.locality}{p.road}{i % 300 + 1}号",
        BRNAME=p.name.removesuffix("有限公司") + f"有限公司{p.district}分公司",
        BRN_CREDIT_CODE=credit_code(p.area, i + 30000),
        BRN_REG_ORG=p.authority,
        BRPRINCIPAL=person_name(i + 201),
        BRPRIPID=f"{p.area}{i + 30000:012d}",
        BRREGNO=p.area + f"{i + 30000:09d}",
        CBUITEM=p.tag["opscope"],
        INREASON="未按规定期限公示年度报告",
        OUTREASON="已补报年度报告并公示",
        INDATE=iso(p.as_of - timedelta(days=300)),
        OUTDATE=iso(p.as_of - timedelta(days=60)),
        YC_REGORG=p.authority,
        YR_REGORG=p.authority,
        CONGROCUR="CNY",
        RELATECREDITCODE=p.related_credit,
        PUBLICDATE=iso(p.published),
        INREGORG=p.authority,
        OUTREGORG=p.authority,
        INSN=f"{p.short}市监列异〔{p.as_of.year}〕{i}号",
        OUTSN=f"{p.short}市监移异〔{p.as_of.year}〕{i}号",
        ABUITEM="依法须经批准的项目，经相关部门批准后方可开展经营活动",
        ANCHEDATE=f"{p.as_of.year}-06-15",
        ANCHEYEAR=str(p.as_of.year - 1),
        APPRDATE=iso(p.as_of - timedelta(days=65)),
        DOM=p.address,
        DOMDISTRICT=p.area,
        EMAIL=f"office{i}@example.com",
        ENTITYTYPE="企业法人",
        ENTNAMEENG=f"Chenghe Industrial {i} Co., Ltd.",
        ENTNAME_OLD=p.name.replace("有限公司", "发展有限公司"),
        INDUSTRYCOCODE=p.industry[3],
        INDUSTRYCONAME=p.industry[4],
        OPFROM=iso(p.founded),
        OPTO=iso(p.founded + timedelta(days=365 * 30)),
        ORIREGNO=p.regno,
        ZSOPSCOPE=p.tag["opscope"],
        RQORGCODE=p.orgcode,
        RQREGNO=p.regno,
        RQCREDITCODE=p.credit,
        RQNAME=p.name,
        DATA_SOURCE_TYPE="工商登记",
        SIGNAL_NOTES="历史经营异常已完成整改并移出",
        CUST_NAME=p.name,
        COURTNO=p.case + "协助执行通知书",
        SHAREAM=amount(p.target / 10000, 2),
        ASSIGNEE=person_name(i + 201),
        ASSIGNEE_ITYPE="居民身份证",
        ASSIGNEE_LTYPE="居民身份证",
        ASSIGNEE_TYPE="自然人",
        EXECUTION="办理股权转让登记",
        EX_NOTICE_NO=p.case + "协助执行通知书",
        INAME_ITYPE="居民身份证",
        INAME_LTYPE="居民身份证",
        INAME_TYPE="自然人",
        JUDGMENT_NO=p.case,
        CFPERIOD="三年",
        FPERIOD="三年",
        CFROFROM=iso(p.filed + timedelta(days=1095)),
        CFROTO=iso(p.filed + timedelta(days=2190)),
        EXPIRATION_REASON="冻结期限届满",
        EXPIRATION_DATE=iso(p.filed + timedelta(days=1095)),
        FREEZE_FLAG="冻结",
        ID_TYPE="居民身份证",
        LICENCE_TYPE="居民身份证",
        LICENCE_NO=p.certificate,
        MARKET_CREDIT_CODE=p.related_credit,
        MARKET_NAME=p.related_name,
        REGIST_NO=p.area + f"{i + 20000:09d}",
        SHAREAM_UNIT="万元",
        HOLDERAMT=str((100 + i % 90) * 10000),
        HOLDERRTO="0.50",
        LIMITHOLDERAMT=str((100 + i % 90) * 4000),
        UNLIMHOLDERAMT=str((100 + i % 90) * 6000),
        SHARESTYPE="人民币普通股",
        SHHOLDERCREDITCODE=p.related_credit,
        SHHOLDERNAME=p.related_name,
        SHHOLDERNATURE="境内非国有法人",
        SHHOLDERNATURECODE="2",
        SHHOLDERREGNO=p.area + f"{i + 20000:09d}",
        SHHOLDERTYPE="企业法人",
        SHHOLDERTYPECODE="1",
    )
    if table == "T_SAIC_BASIC":
        result.update(
            REGCAP=p.tag["cert_capt_amt"],
            RECCAP=p.tag["org_capt_amt"],
            ESDATE=p.tag["found_dt"],
            FRNAME=p.person,
            REGORGPROVINCE=p.province_code,
            REGORGCITY=p.city_code,
            REGORGDISTRICT=p.area,
        )
    if table == "T_SAIC_SHAREHOLDER":
        result.update(
            REGCAP=p.tag["cert_capt_amt"],
            SUBCONAM=amount(dec(p.tag["cert_capt_amt"]) / 10000, 8),
            SUMCONAM=amount(dec(p.tag["cert_capt_amt"]) / 10000, 8),
            FUNDEDRATIO="1.00",
            INVSUMFUNDEDRATIO="1.00",
        )
    if table.endswith("TRADEMARK"):
        result["TYPE"] = "商品商标"
    if related:
        result["ENTSTATUS"] = "存续"
    return result


def credit_value(field, value, p, table):
    desc, raw = field["source_description"], field["source_key"]
    if raw == "PB01AR01":
        return p.birth.isoformat()
    if raw == "PB01AD01":
        return "1" if p.sex == "男" else "2"
    if re.search(r"D\d{2}$", raw):
        return value
    if desc in {"手机号码", "配偶手机号码"}:
        return f"139{p.index + 10000000:08d}"
    if desc in {"联系电话", "住宅电话", "单位电话", "家庭电话", "配偶联系电话"}:
        return (
            p.phone
            if int(re.search(r"\d+", field["type"])[0]) >= 13
            else f"139{p.index + 10000000:08d}"
        )
    if raw == "PB020Q01":
        return p.spouse
    if raw in {"PB01AQ02", "PB01AQ03", "PB030Q01"}:
        return f"{p.locality}{p.road}{100 + p.index % 600}号{1 + p.index % 12}单元{201 + p.index % 20}室"
    if raw.endswith("I02") and desc == "案号":
        return p.case
    if "标注或声明内容" in desc:
        return "信息主体对该记录提出异议，经核查后已更新。"
    if desc in {"账户标识", "授信协议标识", "对象标识"}:
        length = int(re.search(r"\d+", field["type"])[0])
        return str(p.index)[-length:] if length <= 6 else f"{p.area}{p.as_of.year}{p.index:010d}"
    if desc in {"交易明细信息", "特殊交易明细记录"}:
        return "借款人提前偿还部分本金，账户余额按还款结果更新。"
    if desc in {"逾期总额合计", "逾期本金合计", "逾期总额", "逾期本金", "欠税总额"}:
        return amount(0 if p.index % 7 else 1500 + p.index * 10, 2)
    if desc == "欠税统计时间":
        return p.as_of.isoformat()
    if "法院" in desc:
        return p.court
    if "税务机关" in desc:
        return p.tax
    if "处罚机构" in desc or "许可部门" in desc:
        return p.authority
    if desc in {"案由", "执行案由"}:
        return ("买卖合同纠纷", "借款合同纠纷", "承揽合同纠纷")[p.index % 3]
    if desc == "判决/调解结果":
        return f"被告支付货款{amount(p.target, 2)}元及相应利息。"
    if desc in {"诉讼标的", "申请执行标的"}:
        return f"货款人民币{amount(p.target, 2)}元"
    if desc == "已执行标的":
        return f"已收回人民币{amount(p.performed, 2)}元"
    if desc in {"处罚内容", "处罚决定"}:
        return f"责令改正，罚款人民币{amount(p.penalty, 2)}元。"
    if desc == "违法行为":
        return "商品宣传信息中的销量数据缺少证明材料。"
    if desc == "处罚执行情况":
        return "罚款已缴清，已完成整改"
    if desc == "行政复议结果":
        return "维持原行政处罚决定"
    if "处罚决定书文号" in desc:
        return f"{p.short}市监处罚〔{p.filed.year}〕{100 + p.index}号"
    if desc == "执业资格名称":
        return "中级会计专业技术资格"
    if desc == "颁发机构":
        return f"{p.province}财政厅" if p.province != "上海市" else "上海市财政局"
    if "奖励机构" in desc or "奖励部门" in desc or "认定部门" in desc:
        return f"{p.city}科学技术局"
    if desc in {"奖励内容", "奖励事实", "奖励名称"}:
        return "企业技术改造项目获得年度专项资金支持"
    if "认证部门" in desc:
        return "华信质量认证中心"
    if desc == "认证内容":
        return "质量管理体系认证，覆盖产品设计、生产和售后服务"
    if desc == "资质内容":
        return "科技型中小企业入库认定"
    if desc == "许可内容":
        return "在许可范围内开展预包装食品销售业务"
    if desc == "专利名称":
        return ("一种设备状态监测装置", "一种物料自动分拣装置", "一种节能控制系统")[p.index % 3]
    if desc == "专利号":
        return f"ZL202420{100000 + p.index:06d}.{p.index % 10}"
    if desc in {"出口商品名称", "免验商品名称"}:
        return "工业自动化控制设备"
    if "批准部门" in desc or "监管部门" in desc or desc == "管辖直属局":
        return f"{p.city.removesuffix('市')}海关"
    if desc == "免验号":
        return f"{p.as_of.year}{p.area}{p.index:06d}"
    if desc == "规模":
        return amount(dec(p.tag["cert_capt_amt"]) / 10000, 2)
    if desc == "公用事业单位名称":
        return f"{p.city.removesuffix('市')}供水服务有限公司"
    if desc == "上级机构名称":
        return p.related_name
    if desc == "评级机构名称":
        return "中诚信国际信用评级有限责任公司"
    if table == "PBCPC_PE01AZ_TELPAYMENT" and desc == "机构名称":
        return f"中国电信{p.city.removesuffix('市')}分公司"
    if desc in {"业务管理机构", "查询机构"}:
        return f"{p.city.removesuffix('市')}{p.district}支行"
    return value


def refresh(
    destination,
    baseline_destination,
    source=ROOT / "data/mock-sources",
    baseline=ROOT / "examples/mock",
):
    schema = json.loads((source / "schema.json").read_text())
    manifest = json.loads((source / "manifest.json").read_text())
    original_tags = read_csv(baseline / "CCM_C_CUST_FLAG_INFO.csv")
    identities = read_csv(source / "reference/customer_identity.csv")
    profiles = {r["cust_ind"]: Profile(r, int(r["cust_ind"].split("_")[-1])) for r in original_tags}
    for offset, person in enumerate(identities[1000:]):
        index = 9001 + offset
        tag = {
            **original_tags[offset],
            **person,
            "legal_rep_nm": person["person_name"],
            "act_ctrl_psn_nm": person["person_name"],
        }
        profiles[person["cust_ind"]] = Profile(tag, index)
    substitutions = {}
    for p in profiles.values():
        for key in (
            "cust_nm",
            "unify_credit_code",
            "busin_addr",
            "legal_rep_nm",
            "act_ctrl_psn_nm",
        ):
            substitutions[p.original[key]] = p.tag[key]
        substitutions[f"MOCKP{p.index:013d}"] = p.certificate
        substitutions[f"MR{p.index:013d}"] = p.regno
        substitutions[f"M{p.index:08d}"] = p.orgcode
        old_locality = p.original["busin_addr"].split("模拟")[0]
        substitutions[old_locality.replace("市", "") + "模拟支行"] = p.branch
        substitutions[old_locality + "模拟市场监管机构"] = p.authority
    # Longest-first matching is a single pass: replacements cannot cascade.
    pattern = re.compile(
        "|".join(
            re.escape(k)
            if re.search("[\u4e00-\u9fff]", k)
            else r"(?<![A-Za-z0-9_])" + re.escape(k) + r"(?![A-Za-z0-9_])"
            for k in sorted(substitutions, key=len, reverse=True)
        )
    )

    @lru_cache(maxsize=65536)
    def replace(value):
        if value in substitutions:
            return substitutions[value]
        if value.startswith("{") and "模拟" in value:
            return re.sub(
                r'"(?:\\.|[^"\\])*"',
                lambda m: json.dumps(replace(json.loads(m[0])), ensure_ascii=False),
                value,
            )
        return (
            clean(pattern.sub(lambda m: substitutions[m[0]], value)) if "模拟" in value else value
        )

    def map_record(row):
        return {k: replace(v) for k, v in row.items()}

    assert (
        destination.resolve() != source.resolve()
        and baseline_destination.resolve() != baseline.resolve()
    )
    destination.mkdir(parents=True, exist_ok=True)
    baseline_destination.mkdir(parents=True, exist_ok=True)
    baseline_manifest = json.loads((baseline / "manifest.json").read_text())
    baseline_schema = json.loads((ROOT / "configs/bank/schema.json").read_text())
    stats = Counter()
    for table_name, contract in baseline_schema["tables"].items():
        filename = contract["filename"]
        rows = read_csv(baseline / filename)
        for row in rows:
            before = dict(row)
            if table_name == "CCM_C_CUST_FLAG_INFO":
                row.update(profiles[row["cust_ind"]].tag)
            else:
                row.update(map_record(row))
            if "PROPERTIES" in row:
                # Preserve JSON numbers and all amounts: only string values change.
                p = profiles[row["CUST_ID"]]
                row["PROPERTIES"] = (
                    row["PROPERTIES"]
                    .replace("市场监管机构", "市场监督管理局")
                    .replace("企业资讯平台", "企业财经资讯")
                )
                row["PROPERTIES"] = row["PROPERTIES"].replace("产业投资机构", "嘉禾产业投资")
            stats["baseline_changed_cells"] += sum(row[k] != v for k, v in before.items())
        fields = [
            dict(
                name=c["name"],
                type=c["sql_type"],
                format=c.get("format"),
                mock_required=not c["mock_nullable"],
            )
            for c in contract["columns"]
        ]
        sink = CsvSink(baseline_destination / filename, fields, contract["primary_key"])
        for row in rows:
            sink.add(row)
        info = sink.close()
        baseline_manifest["files"][table_name].update(rows=info["rows"], sha256=info["sha256"])
    write_json(baseline_destination / "manifest.json", baseline_manifest)
    links = {
        (r["table"], int(r["csv_row"]) - 1): profiles[r["cust_ind"]]
        for r in read_csv(source / "reference/source_row_links.csv")
    }
    for table in schema["tables"]:
        filename = table["filename"]
        sink = CsvSink(destination / filename, table["columns"], table["mock_primary_key"])
        with (source / filename).open(encoding="utf-8-sig", newline="") as stream:
            for number, original in enumerate(csv.DictReader(stream), 1):
                row = map_record(original)
                p = (
                    profiles[row["cust_ind"]]
                    if table["domain"] == "transactions"
                    else links[table["sheet"], number]
                )
                if table["domain"] == "business":
                    overrides = business_values(p, table["sheet"])
                    for field in table["columns"]:
                        key, name = field["source_key"], field["name"]
                        if original[name] == "":
                            continue
                        if name in p.tag:
                            row[name] = p.tag[name]
                        elif key in overrides:
                            row[name] = str(overrides[key])
                        if field["type"].upper().startswith("DECIMAL") and row[name] != "":
                            row[name] = amount(
                                row[name], int(field["type"].split(",")[1].rstrip(")"))
                            )
                elif table["domain"] == "credit":
                    for field in table["columns"]:
                        name = field["name"]
                        if original[name] != "":
                            row[name] = (
                                p.tag[name]
                                if name in p.tag
                                else credit_value(field, row[name], p, table["sheet"])
                            )
                else:
                    kind = row["ev_ecd"].removeprefix("MOCK_")
                    descriptions = {
                        "PAYROLL": "代发工资",
                        "SETTLE_IN": "销售货款收款",
                        "SETTLE_OUT": "采购货款支付",
                        "PAYROLL_FUND": "工资资金划入",
                        "CLOSE_SWEEP": "账户资金归集",
                        "CASH_SWEEP": "账户资金归集",
                        "FUNDING": "工资资金划入",
                        "LOAN_DRAW": "贷款发放",
                        "LOAN_REPAY": "贷款还款",
                        "OPEN_DEPOSIT": "开户存入",
                        "TERM_DEPOSIT": "定期存款存入",
                        "REV_ORIGINAL": "转账支出",
                        "REVERSAL": "转账冲正",
                        "FX_IN": "外币货款收款",
                        "FX_OUT": "外币货款支付",
                    }
                    label = descriptions.get(kind, kind.replace("_", " "))
                    for key in (
                        "txn_dsc",
                        "txn_smy_dsc",
                        "long_txn_smy",
                        "txn_pstcrpt",
                        "txn_rmrk",
                    ):
                        if row[key]:
                            row[key] = label if key != "txn_rmrk" else "交易处理成功"
                    row["txn_use"] = (
                        "工资薪金"
                        if kind == "PAYROLL"
                        else "货款结算"
                        if kind.startswith("SETTLE")
                        else "资金调拨"
                    )
                    counter = row["cntrprt_txn_accno"]
                    other_index = stable(counter) % 100000
                    counter_name = (
                        person_name(other_index)
                        if kind == "PAYROLL"
                        else f"{p.city.removesuffix('市')}嘉{person_name(other_index)[-2:]}商贸有限公司"
                    )
                    row["cntrprt_txn_accno_nm"] = counter_name
                    row["cntrprtbookentracnonm"] = counter_name
                    row["cntrprt_trdbrh_nm"] = (
                        f"中国工商银行{p.city.removesuffix('市')}{p.district}支行"
                    )
                    row["txn_insid_nm"] = p.branch
                    if row["mrch_nm"]:
                        row["mrch_nm"] = counter_name
                    if row["agnc_psn_ctc_tel"]:
                        row["agnc_psn_ctc_tel"] = f"139{p.index + 10000000:08d}"
                for key, value in row.items():
                    if "模拟" in value or "合成场景" in value or "合成业务" in value:
                        raise ValueError(
                            f"Unresolved display placeholder: {table['sheet']}.{key}={value}"
                        )
                    if original[key] != value:
                        stats[table["domain"] + "_changed_cells"] += 1
                    if table["domain"] == "credit" and (original[key] == "") != (value == ""):
                        raise ValueError("Credit null mask changed")
                sink.add(row)
        info = sink.close()
        assert info["rows"] == manifest["files"][filename]["rows"]
        manifest["files"][filename] = info
        print(f"Refined {table['sheet']}: {info['rows']:,} rows", flush=True)
    for filename in [k for k in manifest["files"] if k.startswith(("reference/", "expected/"))]:
        rows = read_csv(source / filename)
        fields = list(rows[0]) if rows else None
        if fields is None:
            shutil.copy2(source / filename, destination / filename)
            continue
        sink = CsvSink(destination / filename, aux_fields(fields))
        for original in rows:
            row = map_record(original)
            if filename == "reference/customer_identity.csv":
                p = profiles[row["cust_ind"]]
                row.update(
                    cust_nm=p.name,
                    unify_credit_code=p.credit,
                    person_name=p.person,
                    person_certificate=p.certificate,
                )
            if filename == "expected/customer_tags.csv":
                p = profiles[row["cust_ind"]]
                for key in (
                    "cust_nm",
                    "unify_credit_code",
                    "busin_addr",
                    "legal_rep_nm",
                    "act_ctrl_psn_nm",
                    "cert_province",
                    "cert_city",
                    "cert_district",
                    "industry_cd",
                    "industry_big_cd",
                    "industry_mid_cd",
                    "industry_sml_cd",
                ):
                    row[key] = p.tag[key]
            sink.add(row)
        manifest["files"][filename] = sink.close()
    for path in source.glob("*.json"):
        if path.name not in {"manifest.json", "validation.json"}:
            shutil.copy2(path, destination / path.name)
    shutil.copy2(source / "README.md", destination / "README.md")
    manifest["baseline_tags_sha256"] = hashlib.sha256(
        (baseline_destination / "CCM_C_CUST_FLAG_INFO.csv").read_bytes()
    ).hexdigest()
    manifest["realistic_display_revision"] = "2026-09-19"
    write_json(destination / "manifest.json", manifest)
    write_json(
        destination / "realism-report.json",
        dict(
            stats,
            synthetic=True,
            credit_null_mask_preserved=True,
            case_number_format_source="https://www.court.gov.cn/zixun/xiangqing/16415.html",
        ),
    )
    write_json(
        destination / "realism-profiles.json",
        {
            cid: dict(
                name=p.name,
                province=p.province,
                city=p.city,
                district=p.district,
                age=p.age,
                certificate=p.certificate,
                credit=p.credit,
            )
            for cid, p in profiles.items()
        },
    )
    print(json.dumps(dict(stats), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT / "data/mock-sources")
    parser.add_argument("--baseline", type=Path, default=ROOT / "examples/mock")
    args = parser.parse_args()
    refresh(args.output, args.baseline_output, args.source, args.baseline)
